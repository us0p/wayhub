"""The real Postgres checkpointer (D56). Its tables are written outside the test transaction,
so this test only uses a random thread id and deletes it."""

import uuid
from collections.abc import AsyncIterator

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint

from mentor.agents import checkpoint
from mentor.agents.checkpoint import close_checkpointer, get_checkpointer


@pytest.fixture
async def saver() -> AsyncIterator[BaseCheckpointSaver[str]]:
    saver = await get_checkpointer()
    yield saver
    await close_checkpointer()


async def test_postgres_saver_sets_up_writes_and_deletes_threads(
    saver: BaseCheckpointSaver[str],
) -> None:
    thread = str(uuid.uuid4())
    config: RunnableConfig = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    await saver.aput(config, empty_checkpoint(), {"source": "input", "step": -1}, {})
    assert await saver.aget_tuple(config) is not None
    assert await get_checkpointer() is saver  # created once

    await checkpoint.delete_threads(saver, [thread])

    assert await saver.aget_tuple(config) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@db:5432/x", "postgresql://u:p@db:5432/x"),
        ("postgresql://u@h/x", "postgresql://u@h/x"),
    ],
)
def test_psycopg_dsn(url: str, expected: str) -> None:
    assert checkpoint.psycopg_dsn(url) == expected
