"""The voice interview over its WebSocket (M6), with fake STT/TTS/LLM."""

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.ai.ports import AIProviderError, Transcript
from mentor.auth.models import User
from mentor.interview.engine import InterviewEngine
from mentor.interview.models import (
    Interview,
    InterviewStatus,
    InterviewTurn,
    TurnMode,
    TurnRole,
)
from mentor.quotas.models import QuotaKind, UsageEvent
from mentor.voice.session import VoiceSession

from .helpers import (
    WebSocketClient,
    WebSocketRejectedError,
    csrf_from,
    events,
    has_event,
    login,
    login_and_consent,
)

CHUNK = b"\x00\x00" * 1600  # 100 ms of 16 kHz PCM16


@pytest.fixture(autouse=True)
def fast_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(VoiceSession, "end_of_turn_seconds", 0.0)
    monkeypatch.setattr(VoiceSession, "unfinished_turn_seconds", 0.0)
    monkeypatch.setattr(VoiceSession, "listen_again_seconds", 0.0)
    monkeypatch.setattr(VoiceSession, "reconnect_backoff_seconds", 0.0)


async def _user(client: AsyncClient, db: AsyncSession, **override: int | None) -> User:
    email = f"voz-{uuid.uuid4().hex[:8]}@example.com"
    await login_and_consent(client, email)
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    if override:
        user.quota_override = dict(override)
        await db.commit()
    assert (await client.get("/entrevista")).status_code == 200  # opens the interview
    return user


def _ws(app: FastAPI, client: AsyncClient, **kwargs: Any) -> WebSocketClient:
    return WebSocketClient(app, "/entrevista/voz", cookies=dict(client.cookies), **kwargs)


async def _usage(db: AsyncSession, user: User, kind: QuotaKind) -> int:
    total = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.amount), 0)).where(
            UsageEvent.user_id == user.id, UsageEvent.kind == kind
        )
    )
    return int(total or 0)


async def _turns(db: AsyncSession, user: User) -> list[InterviewTurn]:
    return list(
        await db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.user_id == user.id)
            .order_by(InterviewTurn.seq)
            .execution_options(populate_existing=True)
        )
    )


async def test_rejects_anonymous_connections(app: FastAPI, client: AsyncClient) -> None:
    with pytest.raises(WebSocketRejectedError) as rejected:
        async with _ws(app, client):
            pass
    assert rejected.value.code == 1008


async def test_rejects_users_without_consent(app: FastAPI, client: AsyncClient) -> None:
    await login(client, "sem-consentimento@example.com")

    with pytest.raises(WebSocketRejectedError):
        async with _ws(app, client):
            pass


async def test_rejects_cross_site_origins(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db)

    with pytest.raises(WebSocketRejectedError):
        async with _ws(app, client, origin="https://evil.example"):
            pass


async def test_a_spoken_turn_is_transcribed_answered_and_spoken(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    fake_ai.stt.transcripts = [
        Transcript("meu nome", is_final=False),
        Transcript("Meu nome é Ana.", is_final=True),
    ]

    async with _ws(app, client) as ws:
        assert await ws.receive() == {"type": "ready", "remaining_seconds": 30 * 60}
        await ws.send_bytes(CHUNK)
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(
            lambda r: bool(events(r, "agent_done")) and any(isinstance(m, bytes) for m in r)
        )
        await ws.send_json({"type": "end"})
        ended = await ws.receive_until(has_event("end"))

    assert [(e["text"], e["final"]) for e in events(received, "transcript")] == [
        ("meu nome", False),
        ("Meu nome é Ana.", True),
    ]
    assert events(received, "user_turn") == [{"type": "user_turn", "text": "Meu nome é Ana."}]
    assert "".join(e["text"] for e in events(received, "agent_text")) == "Resposta de teste."
    done = events(received, "agent_done")[0]
    assert done["completed"] is False
    assert 'id="progress-panel"' in done["progress_html"]
    assert fake_ai.tts.texts == ["Resposta de teste."]
    assert events(ended, "end") == [{"type": "end", "reason": "ended"}]

    turns = await _turns(db, user)
    assert [(t.role, t.mode, t.text) for t in turns[-2:]] == [
        (TurnRole.USER, TurnMode.VOICE, "Meu nome é Ana."),
        (TurnRole.ASSISTANT, TurnMode.VOICE, "Resposta de teste."),
    ]
    assert await _usage(db, user, QuotaKind.INTERVIEW_TURN) == 1
    assert await _usage(db, user, QuotaKind.VOICE_SECONDS) == 1  # 0.2 s, rounded up


@pytest.fixture
def agent_keeps_talking(fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each sentence becomes 10 s of audio, so the agent is still speaking for a while."""

    async def long_speech(
        text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        async for _ in text:
            yield b"\x00\x00" * 24_000 * 10

    monkeypatch.setattr(fake_ai.tts, "stream", long_speech)


async def _until_agent_speaks(ws: WebSocketClient) -> None:
    await ws.receive()  # ready
    await ws.send_bytes(CHUNK)  # → the first turn; the agent answers and starts speaking
    await ws.receive_until(lambda r: any(isinstance(m, bytes) for m in r))


async def _assert_still_speaking(ws: WebSocketClient) -> None:
    """Nothing more comes (in particular no `stop_audio`) right after the last event."""
    with pytest.raises(TimeoutError):
        await ws.receive(wait=0.2)


@pytest.mark.usefixtures("agent_keeps_talking")
async def test_the_microphone_is_ignored_while_the_agent_speaks(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    """D71: noise or talking over the agent neither interrupts it nor reaches the STT, and
    isn't counted as voice time."""
    user = await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("Sou a Ana.", is_final=True)]

    async with _ws(app, client) as ws:
        await _until_agent_speaks(ws)
        for _ in range(3):
            await ws.send_bytes(CHUNK)  # over the agent
        await _assert_still_speaking(ws)  # no transcript, no stop_audio, nothing

    assert len(fake_ai.stt.received) == len(CHUNK)  # only what was heard before it spoke
    assert await _usage(db, user, QuotaKind.VOICE_SECONDS) == 1  # 0.1 s, rounded up


async def test_listening_resumes_after_the_agent_finishes(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    await _user(client, db)
    fake_ai.stt.transcripts = [
        Transcript("Sou a Ana.", is_final=True),
        Transcript("Moro em Santos.", is_final=True),
    ]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        await ws.receive_until(has_event("agent_done"))
        await asyncio.sleep(0.05)  # the (tiny) fake audio has "played"
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("agent_done"))

    assert events(received, "user_turn") == [{"type": "user_turn", "text": "Moro em Santos."}]


async def test_a_sentence_left_unfinished_waits_longer_for_the_rest(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause after "Então, eu tive contato" (no full stop) is thinking, not the end."""
    await _user(client, db)
    monkeypatch.setattr(VoiceSession, "unfinished_turn_seconds", 0.4)
    fake_ai.stt.transcripts = [
        Transcript("Então, eu tive contato", is_final=True),
        Transcript("com Java na faculdade.", is_final=True),
    ]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        await ws.receive_until(has_event("transcript"))
        await asyncio.sleep(0.1)  # a pause, but shorter than the wait for an unfinished sentence
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("agent_done"))

    assert events(received, "user_turn") == [
        {"type": "user_turn", "text": "Então, eu tive contato com Java na faculdade."}
    ]


async def test_finals_made_only_of_fillers_are_dropped(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    fake_ai.stt.transcripts = [
        Transcript("Hmm.", is_final=True),
        Transcript("Sou a Ana.", is_final=True),
    ]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("agent_done"))

    speech = [(e["text"], e["final"]) for e in events(received, "transcript")]
    assert speech == [("", True), ("Sou a Ana.", True)]  # "" clears the live bubble
    assert events(received, "user_turn") == [{"type": "user_turn", "text": "Sou a Ana."}]
    texts = [t.text for t in await _turns(db, user) if t.role is TurnRole.USER]
    assert texts == ["Sou a Ana."]


async def test_a_pause_between_sentences_opens_a_new_speech_stream(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google's TTS aborts a stream that gets no text for 5 s (D70); a slow model between
    two sentences must not cut the agent's voice off."""
    await _user(client, db)
    monkeypatch.setattr(VoiceSession, "tts_idle_seconds", 0.1)
    streams: list[list[str]] = []

    async def recording_tts(
        text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        texts: list[str] = []
        streams.append(texts)
        async for chunk in text:
            texts.append(chunk)
            yield b"\x00\x00" * 100

    monkeypatch.setattr(fake_ai.tts, "stream", recording_tts)
    fake_ai.chat.word_delay = 0.15  # a slow model: ~0.3 s from one sentence to the next
    fake_ai.stt.transcripts = [Transcript("Oi.", is_final=True)]
    fake_ai.chat.script("Primeira frase. Segunda frase.")

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        await ws.receive_until(has_event("agent_done"))

    assert streams == [["Primeira frase."], ["Segunda frase."]]  # two streams, nothing lost


async def test_speech_streams_are_rotated(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _user(client, db)
    monkeypatch.setattr(VoiceSession, "segment_seconds", 0.2)
    fake_ai.stt.transcripts = [Transcript(f"parte {i}", is_final=False) for i in range(5)]

    async with _ws(app, client) as ws:
        await ws.receive()
        for _ in range(5):
            await ws.send_bytes(CHUNK)
        await ws.receive_until(lambda r: len(events(r, "transcript")) == 5)

    assert len(fake_ai.stt.languages) == 3  # 2 + 2 + 1 chunks
    assert len(fake_ai.stt.received) == 5 * len(CHUNK)


async def test_muted_audio_is_neither_transcribed_nor_counted(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("antes", False), Transcript("depois", False)]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        await ws.send_json({"type": "mute"})
        await ws.send_bytes(CHUNK * 10)  # 1 s, ignored
        await ws.send_json({"type": "unmute"})
        await ws.send_bytes(CHUNK)
        await ws.receive_until(lambda r: len(events(r, "transcript")) == 2)

    assert len(fake_ai.stt.received) == 2 * len(CHUNK)
    assert len(fake_ai.stt.languages) == 2  # muting closed the first stream
    assert await _usage(db, user, QuotaKind.VOICE_SECONDS) == 1


async def test_voice_minutes_running_out_ends_the_session(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    user = await _user(client, db, voice_seconds=1)

    async with _ws(app, client) as ws:
        assert await ws.receive() == {"type": "ready", "remaining_seconds": 1}
        for _ in range(2):
            await ws.send_bytes(CHUNK * 5)  # 0.5 s
        received = await ws.receive_until(has_event("end"))

    assert events(received, "end") == [{"type": "end", "reason": "voice_limit"}]
    assert await _usage(db, user, QuotaKind.VOICE_SECONDS) == 1


async def test_no_voice_minutes_left_ends_before_listening(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db, voice_seconds=0)

    async with _ws(app, client) as ws:
        assert await ws.receive() == {"type": "end", "reason": "voice_limit"}
        assert await ws.receive() == {"type": "closed"}


async def test_usage_is_recorded_while_streaming(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _user(client, db, voice_seconds=60)
    monkeypatch.setattr(VoiceSession, "usage_flush_seconds", 0.5)

    async with _ws(app, client) as ws:
        await ws.receive()
        for _ in range(5):
            await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("remaining"))

    assert events(received, "remaining") == [{"type": "remaining", "seconds": 59}]


async def test_the_turn_quota_ends_voice_mode(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db, interview_turn=0)
    fake_ai.stt.transcripts = [Transcript("Olá.", is_final=True)]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("end"))

    assert events(received, "end") == [{"type": "end", "reason": "turn_quota"}]
    assert not [t for t in await _turns(db, user) if t.role is TurnRole.USER]


def _notices(received: list[dict[str, Any] | bytes], kind: str) -> list[dict[str, Any]]:
    return [e for e in events(received, "notice") if e["kind"] == kind]


async def test_speech_recognition_is_retried_after_a_failed_connection(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    """D73: a passing "reconnecting" notice, then the conversation just goes on."""
    user = await _user(client, db)
    fake_ai.stt.transcripts = [
        AIProviderError("Google Speech-to-Text error 503"),
        Transcript("Meu nome é Ana.", is_final=True),
    ]

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)  # → the connection fails
        await ws.send_bytes(CHUNK)  # → the retry hears this one
        received = await ws.receive_until(has_event("agent_done"))

    assert [(n["attempt"], n["attempts"]) for n in _notices(received, "reconnecting")] == [(1, 3)]
    assert len(_notices(received, "reconnected")) == 1
    assert not events(received, "end")
    assert events(received, "user_turn")[0]["text"] == "Meu nome é Ana."
    assert (await _turns(db, user))[-1].text == "Resposta de teste."


async def test_speech_recognition_failing_three_times_falls_back_to_text(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _user(client, db)
    fake_ai.stt.transcripts = [
        AIProviderError("Google Speech-to-Text error 503", log_detail="STT ServiceUnavailable")
        for _ in range(4)  # the first try and three retries
    ]

    async with _ws(app, client) as ws:
        await ws.receive()
        for _ in range(4):
            await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("end"))

    assert [n["attempt"] for n in _notices(received, "reconnecting")] == [1, 2, 3]
    assert events(received, "end") == [{"type": "end", "reason": "connection"}]
    failure = next(r.getMessage() for r in caplog.records if "voice session failed" in r.message)
    assert "failed in _listen after" in failure and "listening, mic on" in failure
    assert "[STT ServiceUnavailable]" in failure


async def test_speech_synthesis_is_retried_with_the_same_sentence(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("Olá.", is_final=True)]
    spoken: list[str] = []
    attempts = 0

    async def flaky_tts(
        text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        nonlocal attempts
        attempts += 1
        async for chunk in text:
            if attempts == 1:
                raise AIProviderError("Google Text-to-Speech error 503")
            spoken.append(chunk)
            yield b"\x00\x00" * 100

    monkeypatch.setattr(fake_ai.tts, "stream", flaky_tts)

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(
            lambda r: bool(_notices(r, "reconnected")) and any(isinstance(m, bytes) for m in r)
        )

    assert len(_notices(received, "reconnecting")) == 1
    assert spoken == ["Resposta de teste."]  # nothing was lost, nothing repeated


async def test_speech_synthesis_failing_three_times_falls_back_and_keeps_the_reply(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("Olá.", is_final=True)]
    fake_ai.tts.error = AIProviderError("Google Text-to-Speech error 503")

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("end"))

    assert [n["attempt"] for n in _notices(received, "reconnecting")] == [1, 2, 3]
    assert events(received, "end")[0]["reason"] == "connection"
    # The text reply was generated and saved; the text UI shows it after the fallback.
    assert (await _turns(db, user))[-1].text == "Resposta de teste."


async def test_a_reply_that_fails_before_any_text_is_generated_again(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    """A failed reply used to end voice mode at once; it gets the engine's attempts first (D76)."""
    user = await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("Sou dev.", is_final=True)]
    fake_ai.chat.script(AIProviderError("503"), "Legal! Há quanto tempo?")

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("agent_done"))
        await ws.send_json({"type": "end"})
        ended = await ws.receive_until(has_event("end"))

    assert "".join(e["text"] for e in events(received, "agent_text")) == "Legal! Há quanto tempo?"
    assert events(ended, "end") == [{"type": "end", "reason": "ended"}]
    assert (await _turns(db, user))[-1].text == "Legal! Há quanto tempo?"


async def test_a_reply_failing_every_attempt_falls_back_to_text(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    await _user(client, db)
    fake_ai.stt.transcripts = [Transcript("Sou dev.", is_final=True)]
    fake_ai.chat.script(*[AIProviderError("503")] * 3)

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(CHUNK)
        received = await ws.receive_until(has_event("end"))

    assert events(received, "end") == [{"type": "end", "reason": "failure"}]
    assert not events(received, "agent_text")


async def test_an_unanswered_turn_is_answered_when_voice_starts(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    token = csrf_from((await client.get("/entrevista")).text)
    posted = await client.post(
        "/entrevista/turnos", data={"texto": "Trabalho com dados."}, headers={"X-CSRF-Token": token}
    )
    assert posted.status_code == 200
    fake_ai.chat.script("Legal! Há quanto tempo?")

    async with _ws(app, client) as ws:
        await ws.receive()
        received = await ws.receive_until(has_event("agent_done"))

    assert "".join(e["text"] for e in events(received, "agent_text")) == "Legal! Há quanto tempo?"
    assert (await _turns(db, user))[-1].text == "Legal! Há quanto tempo?"
    assert fake_ai.tts.texts == ["Legal!", "Há quanto tempo?"]  # spoken, not only shown


async def test_a_reply_generated_by_the_text_page_is_replayed_and_spoken(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back in text after a failure, the page answers the pending turn; voice opened meanwhile
    waits for that reply and must still speak it."""
    user = await _user(client, db)
    token = csrf_from((await client.get("/entrevista")).text)
    await client.post(
        "/entrevista/turnos", data={"texto": "Trabalho com dados."}, headers={"X-CSRF-Token": token}
    )
    pending = (await _turns(db, user))[-1]
    pending.reply_started_at = func.now()  # the text page's stream is generating it
    await db.commit()

    async def other_stream_finishes(engine: InterviewEngine) -> None:
        engine.db.add(
            InterviewTurn(
                interview_id=pending.interview_id,
                user_id=user.id,
                role=TurnRole.ASSISTANT,
                text="Legal! Há quanto tempo?",
                reply_to_id=pending.id,
            )
        )
        await engine.db.commit()

    monkeypatch.setattr(InterviewEngine, "_wait_for_other_stream", other_stream_finishes)

    async with _ws(app, client) as ws:
        await ws.receive()
        received = await ws.receive_until(
            lambda r: bool(events(r, "agent_done")) and any(isinstance(m, bytes) for m in r)
        )

    assert "".join(e["text"] for e in events(received, "agent_text")) == "Legal! Há quanto tempo?"
    assert fake_ai.tts.texts == ["Legal!", "Há quanto tempo?"]


async def test_page_offers_voice_mode_and_explains_why_it_ended(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db)

    page = await client.get("/entrevista?voz=voice_limit")

    assert "data-voice-start" in page.text
    assert 'id="voice-panel"' in page.text
    assert 'data-exhausted="false"' in page.text
    assert "30 min de voz restantes" in page.text
    assert '/static/js/voice.js"' in page.text
    assert "Seus minutos de conversa por voz acabaram." in page.text
    assert 'id="voice-noise-tip" role="status" class="mt-3 hidden' in page.text  # until noisy
    assert " style=" not in page.text and " hx-on" not in page.text  # CSP (D37)


async def test_page_has_a_toast_for_connection_notices_and_shows_the_failure_once(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db)

    normal = await client.get("/entrevista")
    failed = await client.get("/entrevista?voz=connection")
    broken = await client.get("/entrevista?voz=failure")

    assert 'id="toast"' in normal.text and "data-show=" not in normal.text
    assert "Erro de conexão, reconectando" in normal.text
    assert "Não consegui restabelecer a conexão de voz." in failed.text
    assert "data-show=" in failed.text
    assert "A conversa por voz foi interrompida" not in failed.text  # no inline note any more
    # A failed reply is not blamed on the connection (D75).
    assert "A Mari teve um problema para responder" in broken.text
    assert "Não consegui restabelecer a conexão" not in broken.text


async def test_page_marks_voice_as_exhausted_and_ignores_unknown_reasons(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db, voice_seconds=0)

    page = await client.get("/entrevista?voz=<script>")

    assert 'data-exhausted="true"' in page.text
    assert "&lt;script&gt;" not in page.text and "<script>" not in page.text


async def test_oversized_frames_end_the_session(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    await _user(client, db)

    async with _ws(app, client) as ws:
        await ws.receive()
        await ws.send_bytes(b"\x00" * (64 * 1024 + 2))
        received = await ws.receive_until(has_event("end"))

    assert events(received, "end") == [{"type": "end", "reason": "ended"}]


async def test_completing_by_voice_opens_the_closing_modal(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await _user(client, db)
    interview = await db.scalar(select(Interview).where(Interview.user_id == user.id))
    assert interview is not None
    interview.status = InterviewStatus.COMPLETE
    await db.commit()

    after_voice = await client.get("/entrevista?voz=completed")
    plain_reload = await client.get("/entrevista")

    assert "dialog data-auto-open" in after_voice.text
    assert "Sua entrevista terminou!" in after_voice.text
    assert "data-auto-open" not in plain_reload.text
    assert 'id="voice-panel"' not in after_voice.text  # no voice mode on a finished interview
