"""The voice interview WebSocket (D5, D25): binary frames carry PCM16 audio (16 kHz from the
browser, 24 kHz to it); text frames carry JSON control messages and events.

Client → server: audio frames and `{"type": "mute" | "unmute" | "end"}`.
Server → client: audio frames and `{"type": ...}` events (see `_payload`).

The CSRF middleware only sees HTTP requests, so the handshake is checked here: same Origin
(browsers always send it on WebSockets), then the session cookie and consent (D41, D44).
"""

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketException, status

from mentor.agents.checkpoint import Checkpointer
from mentor.ai.registry import AIServices
from mentor.auth.deps import DB
from mentor.auth.models import User
from mentor.auth.sessions import cookie_name, resolve_session
from mentor.interview.engine import InterviewEngine
from mentor.legal import has_current_consent
from mentor.voice.session import (
    AgentDone,
    AgentText,
    Control,
    Ended,
    EndReason,
    Incoming,
    Ready,
    Remaining,
    StopAudio,
    UserSpeech,
    UserTurnSaved,
    VoiceEvent,
    VoiceSession,
)
from mentor.web import templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/entrevista")

# 1 s of 16 kHz PCM16 is 32 KB; the browser sends 100 ms frames. Anything bigger is abuse.
MAX_FRAME_BYTES = 64 * 1024


async def voice_user(websocket: WebSocket, db: DB) -> User:
    origin = websocket.headers.get("origin")
    if origin is None or urlsplit(origin).netloc != websocket.headers.get("host"):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    token = websocket.cookies.get(cookie_name())
    session = await resolve_session(db, token) if token else None
    if session is None or not await has_current_consent(db, session.user.id):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return session.user


def _payload(event: VoiceEvent) -> dict[str, Any]:
    match event:
        case Ready(remaining):
            return {"type": "ready", "remaining_seconds": remaining}
        case Remaining(seconds):
            return {"type": "remaining", "seconds": seconds}
        case UserSpeech(text, final):
            return {"type": "transcript", "text": text, "final": final}
        case UserTurnSaved(text):
            return {"type": "user_turn", "text": text}
        case AgentText(text):
            return {"type": "agent_text", "text": text}
        case AgentDone(progress, completed):
            html = templates.get_template("interview/_progress.html").render(progress=progress)
            return {"type": "agent_done", "completed": completed, "progress_html": html}
        case StopAudio():
            return {"type": "stop_audio"}
        case Ended(reason):
            return {"type": "end", "reason": reason.value}


class SocketOutput:
    """Sends to the socket; once a send fails (the client left), later sends are dropped and
    the session ends through the closed incoming stream."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.closed = False

    async def event(self, event: VoiceEvent) -> None:
        await self._send(lambda: self.websocket.send_json(_payload(event)))

    async def audio(self, pcm: bytes) -> None:
        await self._send(lambda: self.websocket.send_bytes(pcm))

    async def _send(self, send: Any) -> None:
        if self.closed:
            return
        try:
            await send()
        except Exception:
            self.closed = True


async def incoming(websocket: WebSocket) -> AsyncIterator[Incoming]:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if (data := message.get("bytes")) is not None:
            if len(data) > MAX_FRAME_BYTES:
                return
            yield data
        elif text := message.get("text"):
            try:
                kind = json.loads(text).get("type")
                yield Control(kind)
            except (ValueError, AttributeError):
                continue  # unknown or malformed control message


@router.websocket("/voz")
async def voice(websocket: WebSocket, db: DB, ai: AIServices, checkpointer: Checkpointer) -> None:
    user = await voice_user(websocket, db)
    await websocket.accept()
    engine = InterviewEngine(db, ai, user, checkpointer)
    out = SocketOutput(websocket)
    interview = await engine.latest()
    if interview is None:
        await out.event(Ended(EndReason.UNAVAILABLE))
    else:
        await VoiceSession(engine, interview, out).run(incoming(websocket))
    if not out.closed:
        with contextlib.suppress(RuntimeError):  # the client closed first
            await websocket.close()
