"""Shared idempotency policy for application commands."""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar
from uuid import UUID

from app.application.commands import CommandResult
from app.application.ports import ProcessedCommand, UnitOfWork
from app.domain.exceptions import IdempotencyConflictError
from app.domain.models import Workout, WorkoutExercise


class CommandOperation(StrEnum):
    CREATE_WORKOUT = "create_workout"
    ADD_WORKOUT_EXERCISE = "add_workout_exercise"
    LOG_WORKOUT_MESSAGE = "log_workout_message"
    COMPLETE_WORKOUT = "complete_workout"
    CANCEL_WORKOUT = "cancel_workout"
    UNDO_WORKOUT_MESSAGE = "undo_workout_message"


ResourceT = TypeVar("ResourceT", Workout, WorkoutExercise)


def verify_replay(existing: ProcessedCommand, requested: ProcessedCommand) -> None:
    legacy_create = (
        existing.operation == "legacy_create_workout"
        and requested.operation == CommandOperation.CREATE_WORKOUT
    )
    if (
        existing.user_id != requested.user_id
        or (
            not legacy_create
            and (
                existing.operation != requested.operation
                or existing.request_hash != requested.request_hash
            )
        )
    ):
        raise IdempotencyConflictError(
            "The idempotency key was already used for a different command"
        )


async def claim_or_replay(
    uow: UnitOfWork,
    requested: ProcessedCommand,
    loader: Callable[[UUID], Awaitable[ResourceT | None]],
) -> CommandResult[ResourceT] | None:
    if await uow.processed_commands.claim(requested):
        return None
    existing = await uow.processed_commands.get(requested.idempotency_key)
    if existing is None:
        raise IdempotencyConflictError("Idempotency claim disappeared unexpectedly")
    verify_replay(existing, requested)
    resource = await loader(existing.resource_id)
    if resource is None:
        raise IdempotencyConflictError("The idempotent result no longer exists")
    return CommandResult(resource, replayed=True)
