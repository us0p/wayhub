"""The real-time voice interview (D5, D26, D27, D30, D31): an asyncio pipeline around the same
`InterviewEngine` as the text UI.

    mic audio ─→ STT (rotated streams) ─→ end of turn ─→ engine ─→ sentences ─→ TTS ─→ speaker
                     └ deaf while the agent speaks: its audio is dropped and the STT stream closed

The session is transport-agnostic: it reads `Incoming` items (PCM16 audio at
`STT_SAMPLE_RATE`, or a `Control`) and writes `VoiceEvent`s and audio (PCM16 at
`TTS_SAMPLE_RATE`) to a `VoiceOutput`; `mentor.voice.routes` adapts it to a WebSocket.

- Turns: STT final results are gathered until a pause long enough passes without new speech
  (`end_of_turn_seconds` after a finished sentence, longer, `unfinished_turn_seconds`, when the
  text stops mid-sentence, D71);
  the utterance becomes a user turn (`TurnMode.VOICE`, one interview-turn quota unit). Turns
  are answered one at a time; speech heard meanwhile waits and becomes the next turn.
- The session does not listen while the agent speaks (D71): microphone audio is dropped (and
  not counted as voice seconds) from the moment the agent starts talking until
  `listen_again_seconds` after its audio ends, and the STT stream is closed meanwhile. So
  noise, echo and other voices can't interrupt or confuse it; there is no barge-in.
- Final transcripts made only of fillers or noise ("hm", "ahn") are dropped (D67); Chirp 3's
  confidence is not a real score, so it can't be used to filter.
- Voice seconds are the audio streamed by the browser (D31), recorded every
  `usage_flush_seconds` and when the session ends. The limit is enforced in memory and
  refreshed from the database on each flush (so a second session also counts).
- Any provider failure, a failed reply or a bug ends the session with `FAILURE`; the UI then
  falls back to text in the same interview (D26), where a pending reply is retried.
- Only transcripts are stored; audio is never persisted (D21).
- The database session is shared with the engine and isn't safe for concurrent use, so every
  database access holds `_db`, including a whole reply (the graph may write between yields).
"""

import asyncio
import contextlib
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.errors import describe
from mentor.ai.ports import STT_SAMPLE_RATE, TTS_SAMPLE_RATE
from mentor.interview.checklist import Progress
from mentor.interview.engine import (
    InterviewEngine,
    ReplyChunk,
    ReplyDone,
    ReplyFailed,
    TurnPendingError,
)
from mentor.interview.models import Interview, InterviewStatus, InterviewTurn, TurnMode
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind
from mentor.voice.sentences import SentenceSplitter

log = logging.getLogger(__name__)

STT_BYTES_PER_SECOND = STT_SAMPLE_RATE * 2  # PCM16 mono
TTS_BYTES_PER_SECOND = TTS_SAMPLE_RATE * 2


class Control(StrEnum):
    MUTE = "mute"
    UNMUTE = "unmute"
    END = "end"


type Incoming = bytes | Control


class EndReason(StrEnum):
    ENDED = "ended"  # the user ended voice mode or the connection closed
    COMPLETED = "completed"  # the interview is complete
    VOICE_LIMIT = "voice_limit"  # no voice minutes left (D31)
    TURN_QUOTA = "turn_quota"  # daily interview-turn quota reached (D39)
    FAILURE = "failure"  # provider failure, failed reply or bug: fall back to text (D26)
    UNAVAILABLE = "unavailable"  # no interview in progress


@dataclass(frozen=True)
class Ready:
    remaining_seconds: int | None  # None = unlimited


@dataclass(frozen=True)
class Remaining:
    seconds: int


@dataclass(frozen=True)
class UserSpeech:
    """What the user is saying so far in the current utterance (live transcript)."""

    text: str
    final: bool


@dataclass(frozen=True)
class UserTurnSaved:
    text: str


@dataclass(frozen=True)
class AgentText:
    text: str


@dataclass(frozen=True)
class AgentDone:
    progress: Progress
    completed: bool


@dataclass(frozen=True)
class Ended:
    reason: EndReason


type VoiceEvent = Ready | Remaining | UserSpeech | UserTurnSaved | AgentText | AgentDone | Ended


class VoiceOutput(Protocol):
    async def event(self, event: VoiceEvent) -> None: ...

    async def audio(self, pcm: bytes) -> None: ...


# Hesitations and noise that STT may transcribe on their own; they never make a turn or
# interrupt the agent. ("é" and "sim" are real answers, so they are not here.)
FILLERS = frozenset(
    {"hm", "hmm", "hum", "humm", "mm", "mhm", "ahn", "ah", "eh", "uh", "uhm", "um", "hã", "ã"}
)


def spoken_words(text: str) -> list[str]:
    """The words of `text` that aren't fillers."""
    return [w for w in re.findall(r"\w+", text.casefold()) if w not in FILLERS]


class _Stop(Exception):
    def __init__(self, reason: EndReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class VoiceSession:
    # Silence after a final transcript that ends the user's turn: short when the text ends a
    # sentence, longer when it stops mid-sentence (the person is probably still thinking).
    end_of_turn_seconds = 1.2
    unfinished_turn_seconds = 3.0
    listen_again_seconds = 0.5  # after the agent's audio ends (room echo, playback lag)
    segment_seconds = 270.0  # rotate STT streams before Google's ~5-minute limit (D46)
    usage_flush_seconds = 15.0
    # Google's TTS stream aborts after 5 s without input, and the model can take longer than
    # that between two sentences: the stream is closed after this idle time and a new one is
    # opened for the next sentence (D70).
    tts_idle_seconds = 3.0

    def __init__(self, engine: InterviewEngine, interview: Interview, out: VoiceOutput) -> None:
        self.engine = engine
        self.interview = interview
        self.out = out
        self._db = asyncio.Lock()
        self._tg: asyncio.TaskGroup | None = None
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue()  # None ends the STT stream
        self._utterances: asyncio.Queue[str] = asyncio.Queue()
        self._heard: list[str] = []  # final transcripts of the utterance in progress
        self._end_of_turn: asyncio.Task[None] | None = None
        self._sentences: asyncio.Queue[str | None] | None = None  # current reply → TTS
        self._tts: asyncio.Task[None] | None = None
        self._speaking_until = 0.0  # monotonic estimate of when the browser stops playing
        self._listening = True  # False while the STT stream is closed for the agent's speech
        self._muted = False
        self._opened_at = time.monotonic()
        self._streamed = 0.0  # seconds of audio received
        self._recorded = 0  # seconds already recorded as usage
        self._allowance: float | None = None  # `_streamed` at which minutes run out
        self._flush: asyncio.Task[None] | None = None
        self._pending: InterviewTurn | None = None

    @property
    def db(self) -> AsyncSession:
        return self.engine.db

    # -- lifecycle -----------------------------------------------------------------------

    async def run(self, incoming: AsyncIterator[Incoming]) -> EndReason:
        reason: EndReason | None = None
        try:
            reason = await self._start()
            if reason is None:
                try:
                    async with asyncio.TaskGroup() as tg:
                        self._tg = tg
                        self._spawn(self._receive(incoming))
                        self._spawn(self._listen())
                        self._spawn(self._converse())
                except* _Stop as stops:
                    first = stops.exceptions[0]
                    assert isinstance(first, _Stop)
                    reason = first.reason
        finally:
            await asyncio.shield(self._record_final_usage())
        reason = reason or EndReason.ENDED
        with contextlib.suppress(Exception):  # the client may already be gone
            await self.out.event(Ended(reason))
        log.info("voice session ended: %s (%.0f s of audio)", reason, self._streamed)
        return reason

    async def _start(self) -> EndReason | None:
        await self.db.refresh(self.interview)
        if self.interview.status is not InterviewStatus.IN_PROGRESS:
            return EndReason.UNAVAILABLE
        voice = await quotas.status(self.db, self.engine.user, QuotaKind.VOICE_SECONDS)
        if voice.remaining is not None and voice.remaining <= 0:
            return EndReason.VOICE_LIMIT
        self._allowance = None if voice.remaining is None else float(voice.remaining)
        # A turn left unanswered (a dropped connection, a text turn) is answered first.
        self._pending = await self.engine.pending_turn(self.interview)
        await self.out.event(Ready(voice.remaining))
        return None

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        assert self._tg is not None
        return self._tg.create_task(self._guarded(coro))

    async def _guarded(self, coro: Awaitable[None]) -> None:
        """Unexpected errors end the session (with a text fallback) instead of escaping."""
        try:
            await coro
        except _Stop:
            raise
        except Exception as exc:
            log.warning(
                "voice session failed in %s after %.0f s (%.0f s of audio, %s, %s): %s",
                getattr(coro, "__qualname__", "?").removeprefix("VoiceSession."),
                time.monotonic() - self._opened_at,
                self._streamed,
                "agent speaking" if self._deaf() else "listening",
                "muted" if self._muted else "mic on",
                describe(exc),
            )
            raise _Stop(EndReason.FAILURE) from exc

    # -- incoming audio and controls -----------------------------------------------------

    async def _receive(self, incoming: AsyncIterator[Incoming]) -> None:
        async for item in incoming:
            match item:
                case bytes() if item and not self._muted:
                    if self._deaf():
                        if self._listening:  # close the STT stream; it reopens with later audio
                            self._listening = False
                            self._audio.put_nowait(None)
                        continue
                    self._listening = True
                    self._streamed += len(item) / STT_BYTES_PER_SECOND
                    self._audio.put_nowait(item)
                    if self._allowance is not None and self._streamed >= self._allowance:
                        raise _Stop(EndReason.VOICE_LIMIT)
                    if self._streamed - self._recorded >= self.usage_flush_seconds and (
                        self._flush is None or self._flush.done()
                    ):
                        self._flush = self._spawn(asyncio.shield(self._record_usage()))
                case Control.MUTE:
                    self._muted = True
                    self._audio.put_nowait(None)  # end the STT stream; the next audio reopens it
                case Control.UNMUTE:
                    self._muted = False
                case Control.END:
                    raise _Stop(EndReason.ENDED)
        raise _Stop(EndReason.ENDED)  # the connection closed

    async def _record_final_usage(self) -> None:
        for attempt in range(2):
            try:
                await self._record_usage(final=True)
                return
            except Exception as exc:
                if attempt:
                    log.warning("recording voice usage failed: %s", describe(exc))
                    return
                # A reply interrupted mid-query leaves the session needing a rollback.
                await self.db.rollback()
                await self.db.refresh(self.engine.user)

    async def _record_usage(self, *, final: bool = False) -> None:
        async with self._db:
            # Whole seconds while streaming; the last fraction counts when the session ends.
            total = math.ceil(self._streamed) if final else int(self._streamed)
            if (seconds := total - self._recorded) > 0:
                await quotas.record(self.db, self.engine.user, QuotaKind.VOICE_SECONDS, seconds)
                await self.db.commit()
                self._recorded = total
            if final:
                return
            voice = await quotas.status(self.db, self.engine.user, QuotaKind.VOICE_SECONDS)
        if voice.remaining is not None:
            # Usage recorded elsewhere (another tab) also counts.
            self._allowance = self._recorded + voice.remaining
            left = max(int(self._allowance - self._streamed), 0)
            await self.out.event(Remaining(left))
            if left <= 0:
                raise _Stop(EndReason.VOICE_LIMIT)

    # -- speech to text ------------------------------------------------------------------

    async def _listen(self) -> None:
        while True:
            first = await self._audio.get()
            if first is not None:  # a stream opens with the first audio after a pause
                await self._transcribe(first)

    async def _transcribe(self, first: bytes) -> None:
        async def audio() -> AsyncIterator[bytes]:
            chunk: bytes | None = first
            seconds = 0.0
            while chunk is not None:
                yield chunk
                seconds += len(chunk) / STT_BYTES_PER_SECOND
                if seconds >= self.segment_seconds:
                    return  # rotate: this stream ends, the next audio opens a new one
                chunk = await self._audio.get()

        async for transcript in self.engine.ai.stt.stream(audio()):
            text = transcript.text.strip()
            if not text:
                continue
            words = len(spoken_words(text))
            if transcript.is_final and not words:
                # Noise or a hesitation: not part of the turn. Show what's left of the
                # utterance (maybe nothing) and let the pause keep counting.
                await self.out.event(UserSpeech(" ".join(self._heard), final=True))
                if self._heard:
                    self._restart_end_of_turn()
                continue
            if transcript.is_final:
                self._heard.append(text)
                await self.out.event(UserSpeech(" ".join(self._heard), final=True))
                self._restart_end_of_turn()
            else:
                self._cancel_end_of_turn()  # still talking
                await self.out.event(UserSpeech(" ".join([*self._heard, text]), final=False))

    def _restart_end_of_turn(self) -> None:
        self._cancel_end_of_turn()
        self._end_of_turn = self._spawn(self._finish_utterance())

    def _cancel_end_of_turn(self) -> None:
        if self._end_of_turn is not None:
            self._end_of_turn.cancel()
            self._end_of_turn = None

    async def _finish_utterance(self) -> None:
        finished = " ".join(self._heard).rstrip().endswith((".", "!", "?", "…"))
        await asyncio.sleep(self.end_of_turn_seconds if finished else self.unfinished_turn_seconds)
        text = " ".join(self._heard)
        self._heard.clear()
        self._end_of_turn = None
        if text:
            self._utterances.put_nowait(text)

    # -- the conversation ----------------------------------------------------------------

    async def _converse(self) -> None:
        if self._pending is not None:
            async with self._db:
                await self._reply(self._pending)
        while True:
            parts = [await self._utterances.get()]
            while not self._utterances.empty():  # spoke again while the agent was replying
                parts.append(self._utterances.get_nowait())
            # Shielded: a cancelled query would invalidate the shared connection. The reply
            # itself stays cancellable; the engine releases the turn when it's interrupted.
            turn = await asyncio.shield(self._add_turn(" ".join(parts)))
            await self.out.event(UserTurnSaved(turn.text))
            async with self._db:
                await self._reply(turn)

    async def _add_turn(self, text: str) -> InterviewTurn:
        async with self._db:
            try:
                await quotas.check(self.db, self.engine.user, QuotaKind.INTERVIEW_TURN)
            except quotas.QuotaExceededError:
                raise _Stop(EndReason.TURN_QUOTA) from None
            try:
                turn = await self.engine.add_user_turn(self.interview, text, TurnMode.VOICE)
            except TurnPendingError:  # answered elsewhere (e.g. a text tab) meanwhile
                raise _Stop(EndReason.FAILURE) from None
            await quotas.record(self.db, self.engine.user, QuotaKind.INTERVIEW_TURN)
            await self.db.commit()
            return turn

    async def _reply(self, turn: InterviewTurn) -> None:
        splitter = SentenceSplitter()
        async with contextlib.aclosing(self.engine.respond(self.interview, turn)) as events:
            async for event in events:
                match event:
                    case ReplyChunk(text):
                        await self.out.event(AgentText(text))
                        for sentence in splitter.feed(text):
                            self._say(sentence)
                    case ReplyDone(_, progress, completed):
                        if rest := splitter.flush():
                            self._say(rest)
                        speech = self._end_speech()
                        await self.out.event(AgentDone(progress, completed))
                        if completed:
                            if speech is not None:  # let the goodbye be heard
                                await asyncio.wait({speech})
                            raise _Stop(EndReason.COMPLETED)
                    case ReplyFailed():
                        raise _Stop(EndReason.FAILURE)

    # -- text to speech ------------------------------------------------------------------

    def _say(self, sentence: str) -> None:
        if self._sentences is None:
            self._sentences = asyncio.Queue()
            self._tts = self._spawn(self._speak(self._sentences, previous=self._tts))
        self._sentences.put_nowait(sentence)

    def _end_speech(self) -> asyncio.Task[None] | None:
        if self._sentences is not None:
            self._sentences.put_nowait(None)
            self._sentences = None
        return self._tts

    async def _speak(
        self, sentences: asyncio.Queue[str | None], previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None:  # never interleave two replies' audio
            await asyncio.wait({previous})

        finished = False

        async def texts(first: str) -> AsyncIterator[str]:
            nonlocal finished
            yield first
            while True:
                try:
                    sentence = await asyncio.wait_for(sentences.get(), self.tts_idle_seconds)
                except TimeoutError:
                    return  # idle: end this TTS stream; the next sentence opens another
                if sentence is None:
                    finished = True
                    return
                yield sentence

        while not finished:
            if (first := await sentences.get()) is None:
                return
            async for pcm in self.engine.ai.tts.stream(texts(first)):
                now = time.monotonic()
                self._speaking_until = (
                    max(now, self._speaking_until) + len(pcm) / TTS_BYTES_PER_SECOND
                )
                await self.out.audio(pcm)

    def _deaf(self) -> bool:
        """The agent is speaking (or just finished): the microphone is not listened to."""
        if self._tts is not None and not self._tts.done():
            return True
        return time.monotonic() < self._speaking_until + self.listen_again_seconds
