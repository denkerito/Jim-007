from uuid import uuid4

import pytest

from app.application.idempotency import (
    CommandOperation,
    claim_or_replay,
    verify_replay,
)
from app.application.commands import CancelWorkoutCommand
from app.application.ports import ProcessedCommand
from app.application.services import CancelWorkout
from app.domain.exceptions import IdempotencyConflictError


def _command(**changes) -> ProcessedCommand:
    values = {
        "idempotency_key": "telegram:update:1",
        "user_id": uuid4(),
        "operation": CommandOperation.CREATE_WORKOUT,
        "request_hash": "a" * 64,
        "resource_id": uuid4(),
    }
    values.update(changes)
    return ProcessedCommand(**values)


def test_verify_replay_accepts_matching_and_legacy_create_commands() -> None:
    requested = _command()
    verify_replay(requested, requested)
    legacy = _command(
        user_id=requested.user_id,
        operation="legacy_create_workout",
        request_hash="b" * 64,
        resource_id=requested.resource_id,
    )
    verify_replay(legacy, requested)


@pytest.mark.parametrize("change", ["user_id", "operation", "request_hash"])
def test_verify_replay_rejects_conflicting_commands(change: str) -> None:
    existing = _command()
    replacements = {
        "user_id": uuid4(),
        "operation": CommandOperation.COMPLETE_WORKOUT,
        "request_hash": "b" * 64,
    }
    requested_values = {
        "user_id": existing.user_id,
        "operation": existing.operation,
        "request_hash": existing.request_hash,
        "resource_id": existing.resource_id,
    }
    requested_values[change] = replacements[change]
    requested = _command(**requested_values)
    with pytest.raises(IdempotencyConflictError):
        verify_replay(existing, requested)


class _ProcessedCommands:
    def __init__(
        self,
        existing: ProcessedCommand | None,
        *,
        claimed: bool = False,
    ) -> None:
        self.existing = existing
        self.claimed = claimed

    async def claim(self, command: ProcessedCommand) -> bool:
        return self.claimed

    async def get(self, idempotency_key: str) -> ProcessedCommand | None:
        return self.existing


class _UnitOfWork:
    def __init__(
        self,
        existing: ProcessedCommand | None,
        *,
        claimed: bool = False,
    ) -> None:
        self.processed_commands = _ProcessedCommands(existing, claimed=claimed)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_claim_or_replay_returns_the_existing_resource() -> None:
    existing = _command()
    resource = object()

    async def load(resource_id):
        assert resource_id == existing.resource_id
        return resource

    replay = await claim_or_replay(  # type: ignore[arg-type]
        _UnitOfWork(existing),  # type: ignore[arg-type]
        existing,
        load,  # type: ignore[arg-type]
    )

    assert replay is not None
    assert replay.value is resource
    assert replay.replayed is True


@pytest.mark.asyncio
async def test_claim_or_replay_returns_none_after_claiming_the_command() -> None:
    existing = _command()

    async def load(resource_id):
        raise AssertionError("The resource must not be loaded for a new claim")

    replay = await claim_or_replay(  # type: ignore[arg-type]
        _UnitOfWork(None, claimed=True),  # type: ignore[arg-type]
        existing,
        load,
    )

    assert replay is None


@pytest.mark.asyncio
async def test_claim_or_replay_rejects_a_disappearing_claim() -> None:
    existing = _command()

    async def load(resource_id):
        return object()

    with pytest.raises(IdempotencyConflictError, match="claim disappeared"):
        await claim_or_replay(  # type: ignore[arg-type]
            _UnitOfWork(None),  # type: ignore[arg-type]
            existing,
            load,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_claim_or_replay_rejects_a_missing_result() -> None:
    existing = _command()

    async def load(resource_id):
        return None

    with pytest.raises(IdempotencyConflictError, match="result no longer exists"):
        await claim_or_replay(  # type: ignore[arg-type]
            _UnitOfWork(existing),  # type: ignore[arg-type]
            existing,
            load,
        )


@pytest.mark.asyncio
async def test_cancel_workout_reports_a_disappearing_idempotency_claim() -> None:
    command = CancelWorkoutCommand(
        user_id=uuid4(),
        workout_id=uuid4(),
        idempotency_key="telegram:update:cancel",
        request_hash="a" * 64,
    )

    with pytest.raises(IdempotencyConflictError, match="claim disappeared"):
        await CancelWorkout(lambda: _UnitOfWork(None)).execute(command)  # type: ignore[arg-type]
