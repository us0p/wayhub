"""The interview engine (D14, D30): one implementation shared by the text UI and (M6) voice.

It owns the interview lifecycle and the transcript in our tables, and runs the LangGraph agent
(`mentor.interview.agent`, D54) for each user turn: the graph plans, speaks (streamed),
extracts and applies; the engine forwards the `speak` node's tokens and stores the reply.

The engine is transport-agnostic: callers iterate `respond()` and get `ReplyChunk`s followed
by one `ReplyDone` or `ReplyFailed`.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import StreamMode
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.bundle import AI
from mentor.ai.errors import describe
from mentor.auth.models import User
from mentor.auth.sessions import now
from mentor.i18n import gettext_ as _
from mentor.interview import store
from mentor.interview.agent import InterviewState, TurnContext, build_graph
from mentor.interview.checklist import Progress
from mentor.interview.models import (
    Interview,
    InterviewStatus,
    InterviewTurn,
    TurnMode,
    TurnRole,
)
from mentor.profile import service as profile_service

log = logging.getLogger(__name__)

HISTORY_TURNS = 30  # recent turns the agent sees; the profile summary covers the rest
MAX_TURN_CHARS = 4000
# A reply that started this long ago without finishing is considered dead and may be retried.
STALE_REPLY = timedelta(minutes=2)
WAIT_POLL_SECONDS = 1.0  # how often a second stream checks on a reply being generated
# Generations allowed per user turn (failures, retries, reconnects). After that the turn is
# abandoned and the user sends a new message, which costs quota (D58).
MAX_REPLY_ATTEMPTS = 3
# "messages": tokens as they are generated (we forward the speak node's); "values": state.
STREAM_MODES: list[StreamMode] = ["messages", "values"]


@dataclass(frozen=True)
class ReplyChunk:
    text: str


@dataclass(frozen=True)
class ReplyDone:
    reply: InterviewTurn
    progress: Progress
    completed: bool


@dataclass(frozen=True)
class ReplyFailed:
    retryable: bool = True  # False: attempts exhausted, the user should send a new message


type ReplyEvent = ReplyChunk | ReplyDone | ReplyFailed


class TurnPendingError(Exception):
    """The previous user turn has no reply yet."""


def greeting(user: User) -> str:
    return _(
        "Oi %(name)s, sou a Mari e vou ser sua Mentora nessa entrevista. Vou te fazer "
        "algumas perguntas para montar o seu perfil profissional; é só responder do seu "
        "jeito. Para começar: o que você faz hoje e que tipo de trabalho está buscando?"
    ) % {"name": user.name.split(" ")[0]}


def resume_message() -> str:
    return _("Que bom que você voltou! Vamos continuar de onde paramos.")


def follow_up_greeting() -> str:
    return _(
        "Que bom te ver de novo! O que mudou desde a nossa última conversa, ou o que você "
        "quer acrescentar ao seu perfil?"
    )


def _in_flight(turn: InterviewTurn) -> bool:
    started = turn.reply_started_at
    return started is not None and started >= now() - STALE_REPLY


def _abandoned(turn: InterviewTurn) -> bool:
    """No attempts left and none running: the reply will never come."""
    return turn.reply_attempts >= MAX_REPLY_ATTEMPTS and not _in_flight(turn)


def as_message(turn: InterviewTurn) -> BaseMessage:
    """Our turn as a LangChain message; the turn id makes the merge into state idempotent."""
    if turn.role is TurnRole.USER:
        return HumanMessage(content=turn.text, id=str(turn.id))
    return AIMessage(content=turn.text, id=str(turn.id))


class InterviewEngine:
    def __init__(
        self, db: AsyncSession, ai: AI, user: User, checkpointer: BaseCheckpointSaver[str]
    ) -> None:
        self.db = db
        self.ai = ai
        self.user = user
        self.checkpointer = checkpointer

    # -- interviews --------------------------------------------------------------------------

    async def latest(self) -> Interview | None:
        return await store.latest_interview(self.db, self.user)

    async def open(self) -> Interview:
        """The interview to show: the current one, resuming it if paused, or a new one.

        A completed interview is returned as is; `start_follow_up()` opens a new one.
        """
        interview = await self.latest()
        if interview is None:
            interview = Interview(user_id=self.user.id)
            self.db.add(interview)
            await self.db.flush()
            await self._say(interview, greeting(self.user))
        elif interview.status is InterviewStatus.PAUSED:
            interview.status = InterviewStatus.IN_PROGRESS
            await self._say(interview, resume_message())
        await self.db.commit()
        return interview

    async def start_follow_up(self) -> Interview:
        interview = Interview(user_id=self.user.id)
        self.db.add(interview)
        await self.db.flush()
        await self._say(interview, follow_up_greeting())
        await self.db.commit()
        return interview

    async def pause(self, interview: Interview) -> None:
        if interview.status is InterviewStatus.IN_PROGRESS:
            interview.status = InterviewStatus.PAUSED
            await self.db.commit()

    async def turns(self, interview: Interview) -> list[InterviewTurn]:
        return list(
            await self.db.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.interview_id == interview.id)
                .order_by(InterviewTurn.seq)
            )
        )

    async def pending_turn(self, interview: Interview) -> InterviewTurn | None:
        """The last user turn, if it still has no reply and isn't abandoned."""
        last = await self.db.scalar(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.seq.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if last is None or last.role is not TurnRole.USER or _abandoned(last):
            return None
        return last

    async def _say(self, interview: Interview, text: str) -> InterviewTurn:
        turn = InterviewTurn(
            interview_id=interview.id, user_id=self.user.id, role=TurnRole.ASSISTANT, text=text
        )
        self.db.add(turn)
        await self.db.flush()
        return turn

    async def progress(self) -> Progress:
        return await store.checklist_progress(self.db, self.user)

    # -- turns -------------------------------------------------------------------------------

    async def add_user_turn(
        self, interview: Interview, text: str, mode: TurnMode = TurnMode.TEXT
    ) -> InterviewTurn:
        if await self.pending_turn(interview) is not None:
            raise TurnPendingError
        turn = InterviewTurn(
            interview_id=interview.id,
            user_id=self.user.id,
            role=TurnRole.USER,
            mode=mode,
            text=text.strip()[:MAX_TURN_CHARS],
        )
        self.db.add(turn)
        await self.db.commit()
        return turn

    async def _claim(self, turn: InterviewTurn) -> bool:
        """Atomically mark the reply as started, so concurrent streams don't both generate it."""
        current = now()
        result = await self.db.execute(
            update(InterviewTurn)
            .where(
                InterviewTurn.id == turn.id,
                InterviewTurn.reply_attempts < MAX_REPLY_ATTEMPTS,
                or_(
                    InterviewTurn.reply_started_at.is_(None),
                    InterviewTurn.reply_started_at < current - STALE_REPLY,
                ),
            )
            .values(reply_started_at=current, reply_attempts=InterviewTurn.reply_attempts + 1)
            .returning(InterviewTurn.id)
        )
        claimed = result.first() is not None
        await self.db.commit()
        return claimed

    async def _wait_for_other_stream(self) -> None:
        await asyncio.sleep(WAIT_POLL_SECONDS)

    async def _release(self, turn: InterviewTurn) -> None:
        await self.db.execute(
            update(InterviewTurn).where(InterviewTurn.id == turn.id).values(reply_started_at=None)
        )
        await self.db.commit()

    async def respond(
        self, interview: Interview, turn: InterviewTurn
    ) -> AsyncGenerator[ReplyEvent]:
        """Generate (or replay) the reply to `turn`.

        If another stream is already generating it (e.g. the browser reconnected while the
        first connection is still open), wait for that reply and replay it; if that stream
        dies, take over. Give up only once its claim would be stale anyway.
        """
        deadline = time.monotonic() + STALE_REPLY.total_seconds() + WAIT_POLL_SECONDS
        while True:
            existing = await self.db.scalar(
                select(InterviewTurn).where(InterviewTurn.reply_to_id == turn.id)
            )
            if existing is not None:  # already answered: replay it
                yield ReplyChunk(existing.text)
                await self.db.refresh(interview)
                yield ReplyDone(
                    existing, await self.progress(), interview.status is InterviewStatus.COMPLETE
                )
                return
            if await self._claim(turn):
                break
            await self.db.refresh(turn)
            if _abandoned(turn):
                log.info(
                    "reply to turn %s abandoned after %s attempts", turn.id, MAX_REPLY_ATTEMPTS
                )
                yield ReplyFailed(retryable=False)
                return
            if time.monotonic() > deadline:
                log.warning("reply to turn %s still claimed by another stream", turn.id)
                yield ReplyFailed()
                return
            await self._wait_for_other_stream()

        context = TurnContext(
            db=self.db,
            ai=self.ai,
            user=self.user,
            snapshot=await profile_service.snapshot(self.db, self.user),
            progress=await self.progress(),
            turn_id=turn.id,
        )
        recent = await self._history(interview)
        inputs: InterviewState = {
            # Our transcript is the source of truth: the agent's memory is replaced by its
            # recent window each turn, so it can't drift from what the user saw nor grow
            # without bound (D58).
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *(as_message(t) for t in recent)],
            "plan": None,
            "patch": None,
            "completed": False,
        }
        config: RunnableConfig = {"configurable": {"thread_id": str(interview.id)}}
        final: InterviewState | None = None
        try:
            graph = build_graph(self.checkpointer)
            parts = graph.astream(
                inputs, config, context=context, stream_mode=STREAM_MODES, version="v2"
            )
            try:
                async for part in parts:
                    if part["type"] == "values":
                        final = part["data"]
                    elif part["type"] == "messages":
                        chunk, metadata = part["data"]
                        # Only the speaker's tokens reach the user; plan/extract never do.
                        if (
                            metadata.get("langgraph_node") == "speak"
                            and isinstance(chunk, AIMessageChunk)
                            and (text := chunk.text)
                        ):
                            yield ReplyChunk(text)
            finally:
                # If our consumer goes away mid-stream, close the graph so its running nodes
                # are cancelled instead of generating (and paying) in the background.
                await parts.aclose()  # type: ignore[attr-defined]  # an async generator
            if final is None:
                raise RuntimeError("the interview graph produced no state")
            reply, completed = await self._save_reply(interview, turn, final)
        except Exception as exc:  # provider failure, failed save or a bug: allow a retry
            log.warning("interview reply failed: %s", describe(exc))
            await self.db.rollback()  # also undoes this turn's profile changes, if any
            for obj in (turn, interview, self.user):  # rollback expired them; reload async
                await self.db.refresh(obj)
            await self._release(turn)
            await self.db.refresh(turn)
            yield ReplyFailed(retryable=turn.reply_attempts < MAX_REPLY_ATTEMPTS)
            return
        except BaseException:  # client went away: let a reconnect retry right away
            await asyncio.shield(self._release(turn))
            raise
        yield ReplyDone(reply, await self.progress(), completed)

    async def _save_reply(
        self, interview: Interview, turn: InterviewTurn, final: InterviewState
    ) -> tuple[InterviewTurn, bool]:
        message = final["messages"][-1]
        if not isinstance(message, AIMessage):
            raise RuntimeError("the interview graph did not end with the interviewer's message")
        reply = InterviewTurn(
            id=uuid.UUID(message.id),
            interview_id=interview.id,
            user_id=self.user.id,
            role=TurnRole.ASSISTANT,
            mode=turn.mode,
            text=message.text,
            reply_to_id=turn.id,
        )
        self.db.add(reply)
        completed = bool(final.get("completed", False))
        if completed:
            interview.status = InterviewStatus.COMPLETE
            interview.completed_at = now()
        await self.db.commit()
        return reply, completed

    async def _history(self, interview: Interview) -> list[InterviewTurn]:
        recent = await self.db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.seq.desc())
            .limit(HISTORY_TURNS)
        )
        return list(reversed(list(recent)))
