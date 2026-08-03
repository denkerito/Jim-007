from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.application.commands import RegisterExternalIdentityCommand
from app.application.services import RegisterExternalIdentity
from app.domain.models import ExternalIdentity, LoadUnit, User


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class FakeUserRepository:
    def __init__(self) -> None:
        self.values: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.values.get(user_id)

    async def create(
        self,
        *,
        user_id: UUID,
        locale: str,
        timezone: str,
        preferred_load_unit: LoadUnit,
    ) -> User:
        user = User(
            id=user_id,
            locale=locale,
            timezone=timezone,
            preferred_load_unit=preferred_load_unit,
            created_at=NOW,
            updated_at=NOW,
        )
        self.values[user_id] = user
        return user


class FakeExternalIdentityRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], ExternalIdentity] = {}
        self.locked: list[tuple[str, str]] = []

    async def acquire_registration_lock(
        self, provider: str, provider_subject: str
    ) -> None:
        self.locked.append((provider, provider_subject))

    async def get_by_provider_subject(
        self, provider: str, provider_subject: str
    ) -> ExternalIdentity | None:
        return self.values.get((provider, provider_subject))

    async def create(
        self,
        *,
        identity_id: UUID,
        user_id: UUID,
        provider: str,
        provider_subject: str,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity:
        identity = ExternalIdentity(
            id=identity_id,
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            username=username,
            display_name=display_name,
            created_at=NOW,
        )
        self.values[(provider, provider_subject)] = identity
        return identity

    async def update_profile(
        self,
        identity_id: UUID,
        *,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity:
        key, current = next(
            (item for item in self.values.items() if item[1].id == identity_id)
        )
        updated = current.model_copy(
            update={"username": username, "display_name": display_name}
        )
        self.values[key] = updated
        return updated


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.users = FakeUserRepository()
        self.external_identities = FakeExternalIdentityRepository()
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def commit(self) -> None:
        self.commit_count += 1


@pytest.mark.asyncio
async def test_registers_then_refreshes_external_identity_profile() -> None:
    uow = FakeUnitOfWork()
    service = RegisterExternalIdentity(lambda: uow)  # type: ignore[arg-type]

    created = await service.execute(
        RegisterExternalIdentityCommand(
            provider="telegram",
            provider_subject="12345",
            username=" first_name ",
            display_name=" First   User ",
        )
    )
    assert created.created is True
    assert created.user.locale == "it-IT"
    assert created.user.timezone == "Europe/Rome"
    assert created.user.preferred_load_unit is LoadUnit.KG
    assert created.identity.username == "first_name"
    assert created.identity.display_name == "First User"

    existing = await service.execute(
        RegisterExternalIdentityCommand(
            provider="telegram",
            provider_subject="12345",
            username=None,
            display_name=None,
        )
    )
    assert existing.created is False
    assert existing.user.id == created.user.id
    assert existing.identity.username is None
    assert existing.identity.display_name is None
    assert uow.commit_count == 2
    assert uow.external_identities.locked == [
        ("telegram", "12345"),
        ("telegram", "12345"),
    ]
