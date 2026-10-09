import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI, FakeChatModel, FakeChatModels
from mentor.ai.ports import AIProviderError, Effort
from mentor.auth.models import User
from mentor.auth.sessions import now
from mentor.interview import engine as engine_module
from mentor.interview.agent import FALLBACK_PLAN
from mentor.interview.checklist import ChecklistItem
from mentor.interview.engine import (
    InterviewEngine,
    ReplyChunk,
    ReplyDone,
    ReplyEvent,
    ReplyFailed,
    TurnPendingError,
)
from mentor.interview.models import (
    ChecklistStatus,
    Interview,
    InterviewStatus,
    InterviewTurn,
    TurnRole,
)
from mentor.interview.schemas import ProfilePatch, TurnPlan
from mentor.profile import service as profile_service
from mentor.profile.models import Experience, UserSkill

ALL_DONE: list[dict[str, Any]] = [{"item": item.value, "status": "done"} for item in ChecklistItem]


def plan(brief: str = "Pergunte a próxima coisa.", *, done: bool = False) -> dict[str, Any]:
    return {"reasoning": "notas privadas do planejador", "brief": brief, "done": done}


@pytest.fixture
async def user(db: AsyncSession) -> User:
    email = f"eng-{uuid.uuid4().hex[:8]}@example.com"
    user = User(google_sub=f"dev:{email}", email=email, name="Ana Souza")
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
def interviewer(
    db: AsyncSession, fake_ai: FakeAI, user: User, checkpointer: BaseCheckpointSaver[str]
) -> InterviewEngine:
    return InterviewEngine(db, fake_ai, user, checkpointer)


async def _collect(
    interviewer: InterviewEngine, interview: Interview, turn: InterviewTurn
) -> list[ReplyEvent]:
    return [e async for e in interviewer.respond(interview, turn)]


async def _respond(
    interviewer: InterviewEngine, interview: Interview, text: str
) -> list[ReplyEvent]:
    turn = await interviewer.add_user_turn(interview, text)
    return await _collect(interviewer, interview, turn)


def _text(events: list[ReplyEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, ReplyChunk))


def _calls(fake_ai: FakeAI, *, schema: type | None) -> list[Any]:
    return [c for c in fake_ai.chat.calls if c.schema is schema]


async def test_open_creates_an_interview_with_a_greeting(interviewer: InterviewEngine) -> None:
    interview = await interviewer.open()

    [greeting] = await interviewer.turns(interview)
    assert greeting.role is TurnRole.ASSISTANT
    assert greeting.text.startswith("Oi, Ana!")
    assert await interviewer.open() is interview  # reopening does not greet again
    assert len(await interviewer.turns(interview)) == 1


async def test_a_turn_plans_speaks_extracts_and_applies(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(TurnPlan, plan("Pergunte desde quando ela está na Acme."))
    fake_ai.chat.script("Legal! E desde quando você está na Acme?")
    fake_ai.chat.script_for(
        ProfilePatch,
        {
            "experiences": [
                {
                    "company": "Acme",
                    "title": "Desenvolvedora backend",
                    "start": "2021-03",
                    "is_current": True,
                    "is_technical": True,
                    "new_bullets": ["Mantém APIs em Python e Django"],
                }
            ],
            "skills": [{"name": "Python", "years": 3}, {"name": "Django"}],
            "checklist": [
                {"item": "experiences", "status": "partial", "note": "faltam resultados"},
                {"item": "objective", "status": "done"},
            ],
        },
    )

    events = await _respond(interviewer, interview, "Sou dev backend na Acme, uso Python e Django.")

    assert _text(events) == "Legal! E desde quando você está na Acme?"
    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert not done.completed
    assert done.reply.text == "Legal! E desde quando você está na Acme?"
    assert done.progress.percent == 15  # 1 done + 1 partial of 10

    snap = await profile_service.snapshot(db, user)
    [experience] = snap.experiences
    assert experience.start_date is not None
    assert (experience.company, experience.start_date.isoformat()) == ("Acme", "2021-03-01")
    assert [b.text for b in experience.bullets] == ["Mantém APIs em Python e Django"]
    assert experience.bullets[0].embedding is not None
    assert experience.source_turn_id is not None
    assert {s.skill.name: s.years for s in snap.skills} == {"Python": 3, "Django": None}

    [plan_call] = _calls(fake_ai, schema=TurnPlan)
    [speak_call] = _calls(fake_ai, schema=None)
    [extract_call] = _calls(fake_ai, schema=ProfilePatch)
    assert (plan_call.effort, speak_call.effort, extract_call.effort) == (
        Effort.LOW,
        Effort.LOW,
        None,  # the extraction keeps the provider default (D53)
    )
    assert "<mensagem>\nSou dev backend na Acme" in extract_call.messages[-1].text


async def test_the_speaker_never_sees_internal_notes(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    """D54: the leak came from the reply model seeing (and narrating) the checklist."""
    interview = await interviewer.open()
    fake_ai.chat.script_for(TurnPlan, plan("Pergunte o sobrenome."))

    await _respond(interviewer, interview, "Meu e-mail é ana@example.com")

    [plan_call] = _calls(fake_ai, schema=TurnPlan)
    [speak_call] = _calls(fake_ai, schema=None)
    plan_prompt = "\n".join(m.text for m in plan_call.messages)
    speak_prompt = "\n".join(m.text for m in speak_call.messages)
    assert "Checklist:" in plan_prompt and "identity: missing" in plan_prompt
    assert "Brief da próxima mensagem: Pergunte o sobrenome." in speak_prompt
    for internal in ("identity", "missing", "partial", "notas privadas", "Perfil até agora"):
        assert internal not in speak_prompt


async def test_the_next_turn_sees_the_profile_and_can_update_it_by_ref(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(
        ProfilePatch,
        {"experiences": [{"company": "Acme", "title": "Dev"}], "skills": [{"name": "Go"}]},
    )
    await _respond(interviewer, interview, "Trabalho na Acme como dev, com Go.")
    fake_ai.chat.script_for(
        ProfilePatch,
        {
            "experiences": [{"ref": "E1", "start": "2020", "new_bullets": ["Criou o billing"]}],
            "skills": [{"name": "Go", "years": 4, "experience_refs": ["E1"]}],
        },
    )

    await _respond(interviewer, interview, "Comecei em 2020 e criei o billing, uso Go há 4 anos.")

    plan_prompt = _calls(fake_ai, schema=TurnPlan)[-1].messages[-1].text
    assert "[E1] Experiência: Dev em Acme" in plan_prompt
    snap = await profile_service.snapshot(db, user)
    [experience] = snap.experiences
    assert experience.start_date is not None and experience.start_date.year == 2020
    [skill] = snap.skills
    assert (skill.years, skill.experience_ids) == (4, [experience.id])


async def test_removing_a_fact_also_drops_it_from_skill_evidence(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(
        ProfilePatch,
        {"experiences": [{"company": "Acme", "title": "Dev"}], "skills": [{"name": "Rust"}]},
    )
    await _respond(interviewer, interview, "Trabalhei na Acme com Rust.")
    snap = await profile_service.snapshot(db, user)
    snap.skills[0].experience_ids = [snap.experiences[0].id]
    await db.flush()
    fake_ai.chat.script_for(ProfilePatch, {"remove_refs": ["E1"]})

    await _respond(interviewer, interview, "Na verdade nunca trabalhei na Acme.")

    snap = await profile_service.snapshot(db, user)
    assert snap.experiences == []
    assert snap.skills[0].experience_ids == []


async def test_links_with_unsafe_urls_are_dropped(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(
        ProfilePatch,
        {
            "links": [
                {"kind": "github", "url": "github.com/ana"},
                {"kind": "other", "url": "javascript:alert(1)"},
            ]
        },
    )

    await _respond(interviewer, interview, "Meu GitHub é github.com/ana")

    snap = await profile_service.snapshot(db, user)
    assert [link.url for link in snap.links] == ["https://github.com/ana"]


async def test_completes_only_when_the_plan_is_done_and_the_checklist_too(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    interview = await interviewer.open()
    # The planner wants to end, but the checklist still has gaps: keep going.
    fake_ai.chat.script_for(TurnPlan, plan(done=True))
    fake_ai.chat.script_for(ProfilePatch, {"checklist": ALL_DONE[:-1]})
    events = await _respond(interviewer, interview, "Acho que é isso.")
    assert isinstance(events[-1], ReplyDone) and not events[-1].completed
    assert interview.status is InterviewStatus.IN_PROGRESS

    # Checklist done but the planner still has a question: keep going.
    fake_ai.chat.script_for(TurnPlan, plan("Pergunte o GitHub."))
    fake_ai.chat.script_for(ProfilePatch, {"checklist": ALL_DONE})
    events = await _respond(interviewer, interview, "Não tenho links.")
    assert isinstance(events[-1], ReplyDone) and not events[-1].completed

    fake_ai.chat.script_for(TurnPlan, plan("Agradeça e diga que o perfil está pronto.", done=True))
    fake_ai.chat.script("Perfeito, seu perfil está pronto para revisão!")
    events = await _respond(interviewer, interview, "Não tenho GitHub.")

    done = events[-1]
    assert isinstance(done, ReplyDone) and done.completed
    assert done.reply.text == "Perfeito, seu perfil está pronto para revisão!"
    assert interview.status is InterviewStatus.COMPLETE
    assert interview.completed_at is not None


async def test_a_failed_plan_falls_back_to_a_generic_brief(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    interview = await interviewer.open()  # nothing scripted for the plan: it fails

    events = await _respond(interviewer, interview, "Oi")

    assert isinstance(events[-1], ReplyDone)
    [speak_call] = _calls(fake_ai, schema=None)
    assert FALLBACK_PLAN.brief in speak_call.messages[-1].text


async def test_a_failed_reply_releases_the_turn_for_a_retry(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script(AIProviderError("503"))
    turn = await interviewer.add_user_turn(interview, "Oi")

    assert await _collect(interviewer, interview, turn) == [ReplyFailed()]
    assert turn.reply_started_at is None
    assert await interviewer.pending_turn(interview) is turn

    fake_ai.chat.script("Oi! Me conta mais.")
    assert _text(await _collect(interviewer, interview, turn)) == "Oi! Me conta mais."


async def test_an_unexpected_error_also_releases_the_turn(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script(RuntimeError("bug"))
    turn = await interviewer.add_user_turn(interview, "Oi")

    assert await _collect(interviewer, interview, turn) == [ReplyFailed()]
    assert turn.reply_started_at is None


@pytest.mark.parametrize("failure", [None, RuntimeError("bug")])
async def test_a_failed_extraction_keeps_the_conversation(
    interviewer: InterviewEngine,
    fake_ai: FakeAI,
    db: AsyncSession,
    user: User,
    failure: Exception | None,
) -> None:
    interview = await interviewer.open()
    if failure:
        fake_ai.chat.script_for(ProfilePatch, failure)

    events = await _respond(interviewer, interview, "Moro em Campinas.")

    assert isinstance(events[-1], ReplyDone)
    assert (await profile_service.snapshot(db, user)).is_empty


async def test_a_reply_is_generated_once_and_replayed_afterwards(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script("Primeira resposta.")
    turn = await interviewer.add_user_turn(interview, "Oi")
    await _collect(interviewer, interview, turn)
    calls = len(fake_ai.chat.calls)

    replay = await _collect(interviewer, interview, turn)

    assert _text(replay) == "Primeira resposta."
    assert len(fake_ai.chat.calls) == calls
    replies = await db.scalars(select(InterviewTurn).where(InterviewTurn.reply_to_id == turn.id))
    assert len(list(replies)) == 1


async def test_the_agent_state_is_checkpointed_per_interview_without_duplicates(
    interviewer: InterviewEngine, fake_ai: FakeAI, checkpointer: BaseCheckpointSaver[str]
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(TurnPlan, plan("Pergunte a cidade."), plan("Pergunte o cargo."))
    fake_ai.chat.script("De onde você é?", "E qual o seu cargo?")
    await _respond(interviewer, interview, "Oi")
    await _respond(interviewer, interview, "Sou de Campinas.")

    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": str(interview.id)}})

    assert checkpoint is not None
    values = checkpoint.checkpoint["channel_values"]
    turns = await interviewer.turns(interview)
    assert [m.id for m in values["messages"]] == [str(t.id) for t in turns]  # same ids, once
    assert values["messages"][-1].text == "E qual o seu cargo?"
    assert values["plan"]["brief"] == "Pergunte o cargo."


async def test_only_one_user_turn_may_wait_for_a_reply(interviewer: InterviewEngine) -> None:
    interview = await interviewer.open()
    await interviewer.add_user_turn(interview, "Oi")

    with pytest.raises(TurnPendingError):
        await interviewer.add_user_turn(interview, "Oi de novo")


async def test_pause_resume_and_follow_up(interviewer: InterviewEngine) -> None:
    interview = await interviewer.open()
    await interviewer.pause(interview)
    assert interview.status is InterviewStatus.PAUSED

    assert await interviewer.open() is interview
    assert interview.status is InterviewStatus.IN_PROGRESS
    assert (await interviewer.turns(interview))[-1].text.startswith("Que bom que você voltou")

    interview.status = InterviewStatus.COMPLETE
    follow_up = await interviewer.start_follow_up()
    assert follow_up.id != interview.id
    [greeting] = await interviewer.turns(follow_up)
    assert "acrescentar" in greeting.text
    assert await interviewer.latest() is follow_up


async def test_checklist_progress_is_per_user(
    interviewer: InterviewEngine,
    fake_ai: FakeAI,
    db: AsyncSession,
    checkpointer: BaseCheckpointSaver[str],
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(ProfilePatch, {"checklist": [{"item": "languages", "status": "done"}]})
    await _respond(interviewer, interview, "Falo inglês fluente.")

    other = User(google_sub="dev:other-progress", email="op@example.com", name="Outra")
    db.add(other)
    await db.flush()

    mine = await interviewer.progress()
    theirs = await InterviewEngine(db, fake_ai, other, checkpointer).progress()
    assert {s.item: s.status for s in mine.items}[ChecklistItem.LANGUAGES] is ChecklistStatus.DONE
    assert theirs.percent == 0


async def test_account_deletion_cascades_to_interview_and_profile(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(
        ProfilePatch,
        {"experiences": [{"company": "Acme", "new_bullets": ["x"]}], "skills": [{"name": "SQL"}]},
    )
    await _respond(interviewer, interview, "Acme, SQL.")

    await db.delete(user)
    await db.flush()
    db.expunge_all()

    assert await db.scalar(select(Interview).where(Interview.user_id == user.id)) is None
    assert await db.scalar(select(Experience).where(Experience.user_id == user.id)) is None
    assert await db.scalar(select(UserSkill).where(UserSkill.user_id == user.id)) is None


class _BlockingChatModel(FakeChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    started: asyncio.Event
    release: asyncio.Event

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.started.set()
        await self.release.wait()
        async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
            yield chunk


class _BlockingChatModels(FakeChatModels):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def chat(self, *, effort: Effort | None = None) -> Runnable[LanguageModelInput, BaseMessage]:
        return _BlockingChatModel(
            fake_script=self.fake_script, effort=effort, started=self.started, release=self.release
        )


async def test_a_dropped_stream_releases_the_turn(
    db: AsyncSession, fake_ai: FakeAI, user: User, checkpointer: BaseCheckpointSaver[str]
) -> None:
    chat = _BlockingChatModels()
    ai = FakeAI(chat=chat, embeddings=fake_ai.embeddings, stt=fake_ai.stt, tts=fake_ai.tts)
    interviewer = InterviewEngine(db, ai, user, checkpointer)
    interview = await interviewer.open()
    turn = await interviewer.add_user_turn(interview, "Oi")

    task = asyncio.create_task(_collect(interviewer, interview, turn))
    await asyncio.wait_for(chat.started.wait(), 2)
    assert turn.reply_started_at is not None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    await db.refresh(turn)
    assert turn.reply_started_at is None


async def test_a_second_stream_waits_for_the_first_and_replays_its_reply(
    interviewer: InterviewEngine, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    interview = await interviewer.open()
    turn = await interviewer.add_user_turn(interview, "Oi")
    turn.reply_started_at = now()  # another stream is generating it
    await db.commit()
    waits = 0

    async def other_stream_finishes() -> None:
        nonlocal waits
        waits += 1
        db.add(
            InterviewTurn(
                interview_id=interview.id,
                user_id=turn.user_id,
                role=TurnRole.ASSISTANT,
                text="Resposta do outro stream.",
                reply_to_id=turn.id,
            )
        )
        await db.commit()

    monkeypatch.setattr(interviewer, "_wait_for_other_stream", other_stream_finishes)

    events = await _collect(interviewer, interview, turn)

    assert waits == 1
    assert _text(events) == "Resposta do outro stream."
    assert isinstance(events[-1], ReplyDone)


async def test_a_second_stream_takes_over_when_the_first_dies(
    interviewer: InterviewEngine,
    fake_ai: FakeAI,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interview = await interviewer.open()
    turn = await interviewer.add_user_turn(interview, "Oi")
    turn.reply_started_at = now()
    await db.commit()
    fake_ai.chat.script("Gerada pelo segundo stream.")

    async def first_stream_releases() -> None:  # it failed or its client went away
        turn.reply_started_at = None
        await db.commit()

    monkeypatch.setattr(interviewer, "_wait_for_other_stream", first_stream_releases)

    events = await _collect(interviewer, interview, turn)

    assert _text(events) == "Gerada pelo segundo stream."


async def test_a_second_stream_gives_up_after_the_stale_window(
    interviewer: InterviewEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    interview = await interviewer.open()
    turn = await interviewer.add_user_turn(interview, "Oi")
    monkeypatch.setattr(engine_module, "STALE_REPLY", engine_module.timedelta(0))
    monkeypatch.setattr(engine_module, "WAIT_POLL_SECONDS", 0.0)

    async def still_claimed(_: InterviewTurn) -> bool:
        return False

    monkeypatch.setattr(interviewer, "_claim", still_claimed)

    assert await _collect(interviewer, interview, turn) == [ReplyFailed()]


async def test_attempts_per_turn_are_capped_and_the_turn_is_then_abandoned(
    interviewer: InterviewEngine, fake_ai: FakeAI
) -> None:
    """Security: retries/reconnects can't spend unlimited model calls on one quota unit."""
    interview = await interviewer.open()
    turn = await interviewer.add_user_turn(interview, "Oi")
    fake_ai.chat.script(*[AIProviderError("503")] * engine_module.MAX_REPLY_ATTEMPTS)

    results = [await _collect(interviewer, interview, turn) for _ in range(3)]
    calls = len(fake_ai.chat.calls)
    after_cap = await _collect(interviewer, interview, turn)

    assert results == [[ReplyFailed()], [ReplyFailed()], [ReplyFailed(retryable=False)]]
    assert after_cap == [ReplyFailed(retryable=False)]
    assert len(fake_ai.chat.calls) == calls  # no model call once the cap is reached
    assert await interviewer.pending_turn(interview) is None  # abandoned: user may send again
    await interviewer.add_user_turn(interview, "Oi de novo")


async def test_the_agent_memory_follows_our_transcript(
    interviewer: InterviewEngine,
    fake_ai: FakeAI,
    db: AsyncSession,
    checkpointer: BaseCheckpointSaver[str],
) -> None:
    """A reply checkpointed but never committed to our tables must not linger in memory."""
    interview = await interviewer.open()
    events = await _respond(interviewer, interview, "Oi")
    done = events[-1]
    assert isinstance(done, ReplyDone)
    # As if saving the reply had failed after the checkpoint: no reply row, turn released.
    await db.delete(done.reply)
    turn = await interviewer.pending_turn(interview)
    assert turn is not None
    turn.reply_started_at = None
    await db.commit()

    await _collect(interviewer, interview, turn)

    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": str(interview.id)}})
    assert checkpoint is not None
    ids = [m.id for m in checkpoint.checkpoint["channel_values"]["messages"]]
    assert ids == [str(t.id) for t in await interviewer.turns(interview)]
    assert str(done.reply.id) not in ids


async def test_overlong_values_are_clipped_instead_of_failing_the_patch(
    interviewer: InterviewEngine, fake_ai: FakeAI, db: AsyncSession, user: User
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(
        ProfilePatch,
        {
            "contact": {"city": "C" * 500, "phone": "9" * 100},
            "preferences": {"target_roles": ["R" * 300]},
            "languages": [{"language": "L" * 200, "level": "fluente"}],
            "experiences": [{"company": "Acme", "employment_type": "E" * 100}],
        },
    )

    await _respond(interviewer, interview, "...")

    snap = await profile_service.snapshot(db, user)
    assert snap.profile is not None
    assert (len(snap.profile.city or ""), len(snap.profile.phone or "")) == (120, 40)
    assert [len(r) for r in snap.profile.target_roles] == [120]
    assert len(snap.languages[0].language) == 80
    assert len(snap.experiences[0].employment_type or "") == 60


async def test_a_failed_save_releases_the_turn_and_undoes_the_profile_changes(
    interviewer: InterviewEngine,
    fake_ai: FakeAI,
    db: AsyncSession,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interview = await interviewer.open()
    fake_ai.chat.script_for(ProfilePatch, {"experiences": [{"company": "Acme"}]})
    turn = await interviewer.add_user_turn(interview, "Trabalho na Acme.")

    async def failing_save(*_: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(interviewer, "_save_reply", failing_save)

    events = await _collect(interviewer, interview, turn)

    assert events[-1] == ReplyFailed()  # after the streamed chunks: the UI shows a retry
    assert turn.reply_started_at is None
    assert (await profile_service.snapshot(db, user)).experiences == []
