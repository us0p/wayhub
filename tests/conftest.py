"""Root fixtures. Unit tests get a throwaway environment; anything that touches the database
lives under tests/integration and uses its transactional fixtures."""

import os
import secrets
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

# Must run before any settings are read. Real values (CI, local test DB) take precedence.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused@localhost:1/unused")

from mentor.db import import_models
from mentor.main import create_app
from mentor.settings import get_settings

import_models()


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    """Clear the settings cache around tests that change the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def anon_client() -> AsyncIterator[AsyncClient]:
    """Client for pages that never touch the database."""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c
