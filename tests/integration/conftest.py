"""Integration fixtures: every test runs inside a transaction that is rolled back, so tests are
isolated and leave the (already migrated) test database untouched. App code may call
`commit()` freely: with `join_transaction_mode="create_savepoint"` it only releases a
savepoint."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mentor.agents.checkpoint import get_checkpointer
from mentor.ai.adapters.fake import FakeAI
from mentor.ai.registry import get_ai
from mentor.db import get_session
from mentor.main import create_app
from mentor.settings import get_settings


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[Any]:
    engine = create_async_engine(get_settings().database_dsn)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine: Any) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with maker() as session:
            yield session
        await transaction.rollback()


@pytest.fixture
def checkpointer() -> BaseCheckpointSaver[str]:
    """In-memory LangGraph checkpointer; the Postgres one is covered in test_checkpoint.py."""
    return InMemorySaver()


@pytest.fixture
def app(db: AsyncSession, fake_ai: FakeAI, checkpointer: BaseCheckpointSaver[str]) -> FastAPI:
    """The app on the test transaction, with the test's `fake_ai` and `checkpointer`."""
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_ai] = lambda: fake_ai
    app.dependency_overrides[get_checkpointer] = lambda: checkpointer
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
