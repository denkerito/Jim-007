"""Application use cases for the incremental workout lifecycle."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.application.commands import (
    AddExerciseToWorkoutCommand,
    CommandResult,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
    ExistingExerciseReference,
)
from app.application.ports import ProcessedCommand, UnitOfWork, UnitOfWorkFactory
from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    IdempotencyConflictError,
    InvalidWorkoutStateError,
    NotFoundError,
    WorkoutNotEditableError,
)
from app.domain.models import Load, PerformedSet, Workout, WorkoutExercise, WorkoutStatus
from app.domain.normalization import clean_required_text, normalize_exercise_name


ResourceT = TypeVar("ResourceT", Workout, WorkoutExercise)


def _verify_replay(existing: ProcessedCommand, requested: ProcessedCommand) -> None:
    legacy_create = (
        existing.operation == "legacy_create_workout"
        and requested.operation == "create_workout"
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


async def _claim_or_replay(
    uow: UnitOfWork,
    requested: ProcessedCommand,
    loader: Callable[[UUID], Awaitable[ResourceT | None]],
) -> CommandResult[ResourceT] | None:
    if await uow.processed_commands.claim(requested):
        return None
    existing = await uow.processed_commands.get(requested.idempotency_key)
    if existing is None:
        raise IdempotencyConflictError("Idempotency claim disappeared unexpectedly")
    _verify_replay(existing, requested)
    resource = await loader(existing.resource_id)
    if resource is None:
        raise IdempotencyConflictError("The idempotent result no longer exists")
    return CommandResult(resource, replayed=True)


class CreateWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: CreateWorkoutCommand) -> CommandResult[Workout]:
        workout_id = uuid4()
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation="create_workout",
            request_hash=command.request_hash,
            resource_id=workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await _claim_or_replay(
                uow,
                processed,
                lambda resource_id: uow.workouts.get_by_id(resource_id, command.user_id),
            )
            if replay is not None:
                return replay

            user = await uow.users.get_by_id(command.user_id)
            if user is None:
                raise NotFoundError("User not found")
            active = await uow.workouts.get_active_draft(command.user_id)
            if active is not None:
                raise ActiveWorkoutExistsError(active.id)
            performed_on = command.performed_on or datetime.now(
                ZoneInfo(user.timezone)
            ).date()
            workout = await uow.workouts.create(
                workout_id=workout_id,
                user_id=command.user_id,
                performed_on=performed_on,
                notes=command.notes,
            )
            await uow.commit()
            return CommandResult(workout, replayed=False)


class AddExerciseToWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, command: AddExerciseToWorkoutCommand
    ) -> CommandResult[WorkoutExercise]:
        occurrence_id = uuid4()
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation="add_workout_exercise",
            request_hash=command.request_hash,
            resource_id=occurrence_id,
        )
        async with self._uow_factory() as uow:
            async def load_occurrence(resource_id: UUID) -> WorkoutExercise | None:
                workout = await uow.workouts.get_by_id(command.workout_id, command.user_id)
                if workout is None:
                    return None
                return next((item for item in workout.exercises if item.id == resource_id), None)

            replay = await _claim_or_replay(uow, processed, load_occurrence)
            if replay is not None:
                return replay

            user = await uow.users.get_by_id(command.user_id)
            if user is None:
                raise NotFoundError("User not found")
            workout = await uow.workouts.get_for_update(
                command.workout_id, command.user_id
            )
            if workout is None:
                raise NotFoundError("Workout not found")
            if workout.status is not WorkoutStatus.DRAFT:
                raise WorkoutNotEditableError("A completed workout cannot be changed")

            if isinstance(command.exercise, ExistingExerciseReference):
                exercise = await uow.exercises.get_by_id(
                    command.exercise.exercise_id, command.user_id
                )
                if exercise is None:
                    raise NotFoundError("Exercise not found")
            else:
                name = clean_required_text(command.exercise.name)
                if not name:
                    raise InvalidWorkoutStateError("Exercise name must not be blank")
                exercise = await uow.exercises.get_or_create(
                    exercise_id=uuid4(),
                    user_id=command.user_id,
                    name=name,
                    normalized_name=normalize_exercise_name(name),
                )

            set_values = tuple(
                (
                    uuid4(),
                    item.repetitions,
                    (
                        Load(
                            value=item.load_value,
                            unit=item.load_unit or user.preferred_load_unit,
                        )
                        if item.load_value is not None
                        else None
                    ),
                    item.notes,
                )
                for item in command.sets
            )
            occurrence = await uow.workouts.append_exercise(
                workout_id=workout.id,
                user_id=command.user_id,
                occurrence_id=occurrence_id,
                exercise=exercise,
                notes=command.notes,
                sets=set_values,
            )
            workout.with_exercise(occurrence)
            await uow.commit()
            return CommandResult(occurrence, replayed=False)


class CompleteWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: CompleteWorkoutCommand) -> CommandResult[Workout]:
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation="complete_workout",
            request_hash=command.request_hash,
            resource_id=command.workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await _claim_or_replay(
                uow,
                processed,
                lambda resource_id: uow.workouts.get_by_id(resource_id, command.user_id),
            )
            if replay is not None:
                return replay

            workout = await uow.workouts.get_for_update(
                command.workout_id, command.user_id
            )
            if workout is None:
                raise NotFoundError("Workout not found")
            if workout.status is WorkoutStatus.COMPLETED:
                completed = workout
            else:
                workout.as_completed(datetime.now(timezone.utc))
                completed = await uow.workouts.complete(workout.id, command.user_id)
            await uow.commit()
            return CommandResult(completed, replayed=False)
