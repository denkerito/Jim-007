import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.config import get_settings
from app.infrastructure.database.models import AppUser, ExternalIdentity


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("INTERNAL_API_TOKEN", "integration-secret")
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("LLM_MODEL", "gemini-3.5-flash-lite")
os.environ.setdefault("GEMINI_API_KEY", "fake")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine") as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")
        async_url = async_url.replace("postgresql://", "postgresql+asyncpg://")
        os.environ["DATABASE_URL"] = async_url
        get_settings.cache_clear()
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        command.upgrade(config, "head")
        yield async_url


@pytest_asyncio.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE processed_command, performed_set, workout_exercise, "
                "workout, exercise, external_identity, app_user CASCADE"
            )
        )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def user_id(session_factory: async_sessionmaker[AsyncSession]):
    value = uuid4()
    async with session_factory() as session:
        session.add(AppUser(id=value))
        await session.commit()
    return value


@pytest_asyncio.fixture
async def telegram_identity_factory(session_factory: async_sessionmaker[AsyncSession]):
    async def create(subject: int) -> UUID:
        user_id = uuid4()
        async with session_factory() as session:
            session.add(AppUser(id=user_id))
            session.add(
                ExternalIdentity(
                    id=uuid4(), user_id=user_id, provider="telegram",
                    provider_subject=str(subject),
                )
            )
            await session.commit()
        return user_id

    return create
