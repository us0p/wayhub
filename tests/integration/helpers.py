"""Helpers shared by integration tests."""

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

from httpx import AsyncClient, Response


def csrf_from(html: str) -> str:
    marker = '"X-CSRF-Token": "'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


async def login(
    client: AsyncClient, email: str = "ana@example.com", plan: str = "free"
) -> Response:
    return await client.post(
        "/auth/dev-login", data={"email": email, "name": "Ana Souza", "plan": plan}
    )


async def login_and_consent(
    client: AsyncClient, email: str = "ana@example.com", plan: str = "free"
) -> str:
    """Log in, accept the legal documents and return the CSRF token for later POSTs."""
    await login(client, email, plan)
    page = await client.get("/consentimento")
    token = csrf_from(page.text)
    response = await client.post(
        "/consentimento", data={"aceito": "sim"}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 303
    return token


class WebSocketRejectedError(Exception):
    def __init__(self, code: int | None) -> None:
        super().__init__(f"websocket rejected with code {code}")
        self.code = code


class WebSocketClient:
    """A minimal in-process ASGI WebSocket client (httpx has none). It runs the app in the
    test's event loop, so the app shares the test's transactional database session."""

    def __init__(
        self, app: Any, path: str, *, cookies: Mapping[str, str], origin: str = "http://test"
    ) -> None:
        self.app = app
        self.path = path
        self.headers = [(b"host", b"test"), (b"origin", origin.encode())]
        if cookies:
            cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
            self.headers.append((b"cookie", cookie.encode()))
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.close_code: int | None = None

    async def __aenter__(self) -> "WebSocketClient":
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": self.path,
            "raw_path": self.path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": self.headers,
            "server": ("test", 80),
            "client": ("127.0.0.1", 50000),
            "subprotocols": [],
            "state": {},
        }
        self._task = asyncio.create_task(self.app(scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.close":
            await self._task
            raise WebSocketRejectedError(first.get("code"))
        assert first["type"] == "websocket.accept", first
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=5)

    async def _next(self, wait: float = 5) -> dict[str, Any]:
        return await asyncio.wait_for(self._from_app.get(), timeout=wait)

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def send_json(self, data: object) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive(self, wait: float = 5) -> dict[str, Any] | bytes:
        """The next JSON event or audio frame; `{"type": "closed"}` once the app closes."""
        message = await self._next(wait)
        if message["type"] == "websocket.close":
            self.close_code = message.get("code")
            return {"type": "closed"}
        if message.get("bytes") is not None:
            return bytes(message["bytes"])
        return dict(json.loads(message["text"]))

    async def receive_until(
        self, done: Callable[[list[dict[str, Any] | bytes]], bool], wait: float = 5
    ) -> list[dict[str, Any] | bytes]:
        received: list[dict[str, Any] | bytes] = []
        while not done(received):
            received.append(await self.receive(wait))
        return received


def events(received: list[dict[str, Any] | bytes], kind: str | None = None) -> list[dict[str, Any]]:
    return [m for m in received if isinstance(m, dict) and (kind is None or m["type"] == kind)]


def has_event(kind: str) -> Callable[[list[dict[str, Any] | bytes]], bool]:
    return lambda received: bool(events(received, kind))
