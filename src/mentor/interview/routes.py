"""Text interview UI (D14, D17). The user's message is posted with htmx; the reply streams back
over server-sent events into the agent's bubble (htmx sse extension) and the last event swaps
in the final bubble, the checklist progress and the composer (out-of-band)."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from markupsafe import escape
from starlette.responses import Response

from mentor.agents.checkpoint import Checkpointer
from mentor.ai.registry import AIServices
from mentor.auth.deps import DB, CurrentUser
from mentor.interview.engine import (
    InterviewEngine,
    ReplyChunk,
    ReplyDone,
    ReplyFailed,
    TurnPendingError,
)
from mentor.interview.models import Interview, InterviewStatus, InterviewTurn, TurnRole
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind
from mentor.web import templates
from mentor.web.redirects import redirect

router = APIRouter(prefix="/entrevista")


HEARTBEAT_SECONDS = 5.0
HEARTBEAT = ": ping\n\n"  # an SSE comment: ignored by the browser, keeps the connection alive


def sse(event: str, data: str) -> str:
    """One server-sent event; multi-line data becomes several `data:` lines.

    CR and CRLF also end a line in SSE, so they're normalized first: otherwise text from the
    model could start a forged field (e.g. `event: done`) and cut the stream.
    """
    data = data.replace("\r\n", "\n").replace("\r", "\n")
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"event: {event}\n{lines}\n"


async def with_heartbeat[T](
    events: AsyncGenerator[T], interval: float | None = None
) -> AsyncIterator[T | None]:
    """Yield `events`, plus `None` whenever nothing arrived for `interval` seconds.

    Waiting for the model (retries, fallback, slow first token) can take a while; a silent
    connection may be dropped by the browser, a proxy or a mobile network.
    """
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=interval or HEARTBEAT_SECONDS)
            if not done:
                yield None
                continue
            try:
                item = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield item
    finally:
        # The client went away (or we're done): stop the source and let it clean up (release
        # the turn, close the graph) before the request's database session is closed.
        with anyio.CancelScope(shield=True):
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.wait({pending})
            await events.aclose()


async def get_engine(
    user: CurrentUser, db: DB, ai: AIServices, checkpointer: Checkpointer
) -> InterviewEngine:
    return InterviewEngine(db, ai, user, checkpointer)


Engine = Annotated[InterviewEngine, Depends(get_engine)]


async def _turn_of(engine: InterviewEngine, turn_id: uuid.UUID) -> tuple[Interview, InterviewTurn]:
    """The user's own user turn and its interview, or 404."""
    turn = await engine.db.get(InterviewTurn, turn_id)
    if turn is None or turn.user_id != engine.user.id or turn.role is not TurnRole.USER:
        raise HTTPException(status_code=404)
    interview = await engine.db.get(Interview, turn.interview_id)
    assert interview is not None
    return interview, turn


@router.get("", response_class=HTMLResponse)
async def interview_page(request: Request, engine: Engine) -> Response:
    interview = await engine.open()
    return templates.TemplateResponse(
        request,
        "interview/interview.html",
        {
            "active_nav": "interview",
            "interview": interview,
            "turns": await engine.turns(interview),
            "pending": await engine.pending_turn(interview),
            "progress": await engine.progress(),
            "complete": interview.status is InterviewStatus.COMPLETE,
        },
    )


@router.post("/turnos", response_class=HTMLResponse)
async def post_turn(request: Request, engine: Engine) -> Response:
    text = str((await request.form()).get("texto", "")).strip()
    if not text:
        return Response(status_code=204)
    interview = await engine.latest()
    if interview is None or interview.status is not InterviewStatus.IN_PROGRESS:
        return redirect(request, "/entrevista")
    await quotas.check(engine.db, engine.user, QuotaKind.INTERVIEW_TURN)
    try:
        turn = await engine.add_user_turn(interview, text)
    except TurnPendingError:
        return templates.TemplateResponse(
            request, "interview/_turn_pending.html", {}, status_code=409
        )
    await quotas.record(engine.db, engine.user, QuotaKind.INTERVIEW_TURN)
    await engine.db.commit()
    return templates.TemplateResponse(request, "interview/_posted.html", {"turn": turn})


@router.get("/turnos/{turn_id}/pendente", response_class=HTMLResponse)
async def pending_bubble(request: Request, turn_id: uuid.UUID, engine: Engine) -> Response:
    """A fresh streaming bubble for `turn_id` (the retry button after a failure)."""
    _, turn = await _turn_of(engine, turn_id)
    return templates.TemplateResponse(request, "interview/_pending.html", {"turn": turn})


@router.get("/turnos/{turn_id}/resposta")
async def reply_stream(request: Request, turn_id: uuid.UUID, engine: Engine) -> StreamingResponse:
    interview, turn = await _turn_of(engine, turn_id)

    async def events() -> AsyncIterator[str]:
        async for event in with_heartbeat(engine.respond(interview, turn)):
            match event:
                case None:
                    yield HEARTBEAT
                case ReplyChunk(text):
                    yield sse("chunk", str(escape(text)))
                case ReplyDone(reply, progress, completed):
                    html = templates.get_template("interview/_reply_done.html").render(
                        request=request,
                        turn=turn,
                        reply=reply,
                        progress=progress,
                        complete=completed,
                    )
                    yield sse("done", html)
                case ReplyFailed(retryable):
                    html = templates.get_template("interview/_reply_failed.html").render(
                        request=request, turn=turn, retryable=retryable
                    )
                    yield sse("done", html)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pausar")
async def pause(request: Request, engine: Engine) -> Response:
    if (interview := await engine.latest()) is not None:
        await engine.pause(interview)
    return redirect(request, "/")


@router.post("/nova")
async def follow_up(request: Request, engine: Engine) -> Response:
    interview = await engine.latest()
    if interview is not None and interview.status is InterviewStatus.COMPLETE:
        await engine.start_follow_up()
    return redirect(request, "/entrevista")
