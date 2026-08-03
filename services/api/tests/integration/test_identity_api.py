import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_uow_factory
from app.infrastructure.database.models import AppUser, ExternalIdentity
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer integration-secret"}


@pytest.mark.asyncio
async def test_registration_authentication_defaults_and_profile_refresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "telegram_user_id": 123456789,
                "username": "first_name",
                "display_name": "First User",
            }
            unauthorized = await client.post(
                "/internal/identities/telegram", json=payload
            )
            assert unauthorized.status_code == 401

            created = await client.post(
                "/internal/identities/telegram", headers=_headers(), json=payload
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["locale"] == "it-IT"
            assert body["timezone"] == "Europe/Rome"
            assert body["preferred_load_unit"] == "kg"

            replay = await client.post(
                "/internal/identities/telegram",
                headers=_headers(),
                json={**payload, "username": None, "display_name": None},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["user_id"] == body["user_id"]

            invalid = await client.post(
                "/internal/identities/telegram",
                headers=_headers(),
                json={"telegram_user_id": 0},
            )
            assert invalid.status_code == 422

        async with session_factory() as session:
            users = await session.scalar(select(func.count()).select_from(AppUser))
            identities = await session.scalar(
                select(func.count()).select_from(ExternalIdentity)
            )
            identity = await session.scalar(select(ExternalIdentity))
            assert users == 1
            assert identities == 1
            assert identity is not None
            assert identity.provider == "telegram"
            assert identity.provider_subject == "123456789"
            assert identity.username is None
            assert identity.display_name is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_registration_creates_one_user_and_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    transport = ASGITransport(app=app)
    payload = {
        "telegram_user_id": 987654321,
        "username": "concurrent",
        "display_name": "Concurrent User",
    }
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.post(
                    "/internal/identities/telegram", headers=_headers(), json=payload
                ),
                client.post(
                    "/internal/identities/telegram", headers=_headers(), json=payload
                ),
            )
            assert sorted((first.status_code, second.status_code)) == [200, 201]
            assert first.json()["user_id"] == second.json()["user_id"]

        async with session_factory() as session:
            users = await session.scalar(select(func.count()).select_from(AppUser))
            identities = await session.scalar(
                select(func.count()).select_from(ExternalIdentity)
            )
            assert users == 1
            assert identities == 1
    finally:
        app.dependency_overrides.clear()
