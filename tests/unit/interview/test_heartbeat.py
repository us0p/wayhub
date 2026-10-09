import asyncio
from collections.abc import AsyncIterator

from mentor.interview.routes import with_heartbeat


async def test_heartbeats_fill_the_silence_between_events() -> None:
    async def slow() -> AsyncIterator[str]:
        yield "a"
        await asyncio.sleep(0.05)
        yield "b"

    out = [item async for item in with_heartbeat(slow(), interval=0.01)]

    assert out[0] == "a" and out[-1] == "b"
    assert None in out[1:-1]


async def test_closing_the_stream_cancels_the_source_and_waits_for_its_cleanup() -> None:
    cleaned = asyncio.Event()

    async def forever() -> AsyncIterator[str]:
        try:
            await asyncio.sleep(10)
            yield "never"
        finally:
            await asyncio.sleep(0)  # cleanup that awaits (e.g. releasing the turn)
            cleaned.set()

    stream = with_heartbeat(forever(), interval=0.01)
    assert await anext(stream) is None
    await stream.aclose()

    assert cleaned.is_set()


async def test_stopping_early_closes_the_source() -> None:
    closed = asyncio.Event()

    async def source() -> AsyncIterator[str]:
        try:
            yield "a"
            yield "b"
        finally:
            closed.set()

    stream = with_heartbeat(source(), interval=1)
    assert await anext(stream) == "a"
    await stream.aclose()  # e.g. the client disconnected while "a" was being sent

    assert closed.is_set()
