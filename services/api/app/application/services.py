"""Transport-independent application use cases."""

from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.application.commands import (
    AddExerciseToWorkoutCommand,
    CancelWorkoutCommand,
    CancelWorkoutResult,
    CommandResult,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
    ExistingExerciseReference,
    LogWorkoutMessageCommand,
    LogWorkoutMessageResult,
    NewExerciseReference,
    PerformedSetInput,
    UndoWorkoutMessageCommand,
    UndoWorkoutMessageResult,
)
from app.application.idempotency import CommandOperation, claim_or_replay, verify_replay
from app.application.exercises import create_or_get_exercise
from app.application.ports import ProcessedCommand, UnitOfWork, UnitOfWorkFactory
from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    IdempotencyConflictError,
    InvalidWorkoutDateError,
    NoActiveWorkoutError,
    NotFoundError,
    NothingToUndoError,
    WorkoutNotEditableError,
)
from app.domain.models import (
    Load,
    LoadUnit,
    Workout,
    WorkoutExercise,
    WorkoutStatus,
    WorkoutLogClarificationStatus,
)
from app.domain.normalization import clean_required_text


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = clean_required_text(value)
    return cleaned or None


class CreateWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: CreateWorkoutCommand) -> CommandResult[Workout]:
        workout_id = uuid4()
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation=CommandOperation.CREATE_WORKOUT,
            request_hash=command.request_hash,
            resource_id=workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await claim_or_replay(
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
            if performed_on > datetime.now(ZoneInfo(user.timezone)).date():
                raise InvalidWorkoutDateError("A workout cannot be dated in the future")
            workout = await uow.workouts.create(
                workout_id=workout_id,
                user_id=command.user_id,
                performed_on=performed_on,
                notes=command.notes,
                program_workout_id=command.program_workout_id,
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
            operation=CommandOperation.ADD_WORKOUT_EXERCISE,
            request_hash=command.request_hash,
            resource_id=occurrence_id,
        )
        async with self._uow_factory() as uow:
            async def load_occurrence(resource_id: UUID) -> WorkoutExercise | None:
                workout = await uow.workouts.get_by_id(command.workout_id, command.user_id)
                if workout is None:
                    return None
                return next((item for item in workout.exercises if item.id == resource_id), None)

            replay = await claim_or_replay(uow, processed, load_occurrence)
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

            occurrence = await _append_exercise(
                uow=uow,
                user_id=command.user_id,
                workout_id=workout.id,
                log_batch_id=uuid4(),
                occurrence_id=occurrence_id,
                reference=command.exercise,
                sets=command.sets,
                notes=command.notes,
                preferred_load_unit=user.preferred_load_unit,
            )
            workout.with_exercise(occurrence)
            await uow.workout_log_clarifications.cancel_pending_for_workout(
                command.user_id,
                workout.id,
                terminal_at=datetime.now(timezone.utc),
            )
            await uow.commit()
            return CommandResult(occurrence, replayed=False)


async def _append_exercise(
    *,
    uow: UnitOfWork,
    user_id: UUID,
    workout_id: UUID,
    log_batch_id: UUID,
    occurrence_id: UUID,
    reference: ExistingExerciseReference | NewExerciseReference,
    sets: tuple[PerformedSetInput, ...],
    notes: str | None,
    preferred_load_unit: LoadUnit,
) -> WorkoutExercise:
    if isinstance(reference, ExistingExerciseReference):
        exercise = await uow.exercises.get_by_id(reference.exercise_id, user_id)
        if exercise is None:
            raise NotFoundError("Exercise not found")
    else:
        exercise_result = await create_or_get_exercise(
            uow=uow,
            user_id=user_id,
            name=reference.name,
        )
        exercise = exercise_result.exercise

    set_values = tuple(
        (
            uuid4(),
            item.repetitions,
            (
                Load(
                    value=item.load_value,
                    unit=item.load_unit or preferred_load_unit,
                )
                if item.load_value is not None
                else None
            ),
            _clean_optional_text(item.notes),
        )
        for item in sets
    )
    return await uow.workouts.append_exercise(
        workout_id=workout_id,
        user_id=user_id,
        log_batch_id=log_batch_id,
        occurrence_id=occurrence_id,
        exercise=exercise,
        notes=_clean_optional_text(notes),
        sets=set_values,
    )


class LogWorkoutMessage:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, command: LogWorkoutMessageCommand
    ) -> LogWorkoutMessageResult:
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation=CommandOperation.LOG_WORKOUT_MESSAGE,
            request_hash=command.request_hash,
            resource_id=command.workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await claim_or_replay(
                uow,
                processed,
                lambda resource_id: uow.workouts.get_by_id(resource_id, command.user_id),
            )
            if replay is not None:
                return LogWorkoutMessageResult(
                    workout=replay.value,
                    added_exercises=(),
                    replayed=True,
                )

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

            clarification = None
            if command.clarification_id is not None:
                clarification = await uow.workout_log_clarifications.get_for_update(
                    command.clarification_id, command.user_id
                )
                if (
                    clarification is None
                    or clarification.workout_id != workout.id
                    or clarification.status is not WorkoutLogClarificationStatus.PENDING
                ):
                    raise IdempotencyConflictError(
                        "The workout clarification is no longer pending"
                    )

            added: list[WorkoutExercise] = []
            log_batch_id = uuid4()
            for interpreted in command.exercises:
                reference: ExistingExerciseReference | NewExerciseReference
                if interpreted.catalog_exercise_id is not None:
                    reference = ExistingExerciseReference(
                        kind="existing",
                        exercise_id=interpreted.catalog_exercise_id,
                    )
                else:
                    reference = NewExerciseReference(kind="new", name=interpreted.name)
                occurrence = await _append_exercise(
                    uow=uow,
                    user_id=command.user_id,
                    workout_id=workout.id,
                    log_batch_id=log_batch_id,
                    occurrence_id=uuid4(),
                    reference=reference,
                    sets=interpreted.sets,
                    notes=interpreted.notes,
                    preferred_load_unit=user.preferred_load_unit,
                )
                workout = workout.with_exercise(occurrence)
                added.append(occurrence)

            if clarification is not None:
                await uow.workout_log_clarifications.finish(
                    clarification.id,
                    command.user_id,
                    status=WorkoutLogClarificationStatus.RESOLVED.value,
                    terminal_at=datetime.now(timezone.utc),
                )

            await uow.commit()
            return LogWorkoutMessageResult(
                workout=workout,
                added_exercises=tuple(added),
                replayed=False,
            )


class CancelWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: CancelWorkoutCommand) -> CancelWorkoutResult:
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation=CommandOperation.CANCEL_WORKOUT,
            request_hash=command.request_hash,
            resource_id=command.workout_id,
        )
        async with self._uow_factory() as uow:
            if not await uow.processed_commands.claim(processed):
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise IdempotencyConflictError(
                        "Idempotency claim disappeared unexpectedly"
                    )
                verify_replay(existing, processed)
                return CancelWorkoutResult(
                    workout_id=existing.resource_id,
                    replayed=True,
                )

            workout = await uow.workouts.get_for_update(
                command.workout_id, command.user_id
            )
            if workout is None or workout.status is not WorkoutStatus.DRAFT:
                raise NoActiveWorkoutError("There is no active workout to cancel")
            await uow.workouts.delete(workout.id, command.user_id)
            await uow.commit()
            return CancelWorkoutResult(workout_id=workout.id, replayed=False)


class UndoWorkoutMessage:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, command: UndoWorkoutMessageCommand
    ) -> UndoWorkoutMessageResult:
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation=CommandOperation.UNDO_WORKOUT_MESSAGE,
            request_hash=command.request_hash,
            resource_id=command.workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await claim_or_replay(
                uow,
                processed,
                lambda resource_id: uow.workouts.get_by_id(
                    resource_id, command.user_id
                ),
            )
            if replay is not None:
                return UndoWorkoutMessageResult(
                    workout=replay.value,
                    removed_exercises=(),
                    replayed=True,
                )

            workout = await uow.workouts.get_for_update(
                command.workout_id, command.user_id
            )
            if workout is None or workout.status is not WorkoutStatus.DRAFT:
                raise NoActiveWorkoutError("There is no active workout to update")
            removed = await uow.workouts.delete_last_log_batch(
                workout.id, command.user_id
            )
            if not removed:
                raise NothingToUndoError("The active workout has nothing to undo")
            await uow.workout_log_clarifications.cancel_pending_for_workout(
                command.user_id,
                workout.id,
                terminal_at=datetime.now(timezone.utc),
            )
            updated = await uow.workouts.get_by_id(workout.id, command.user_id)
            if updated is None:
                raise RuntimeError("Updated workout could not be loaded")
            await uow.commit()
            return UndoWorkoutMessageResult(
                workout=updated,
                removed_exercises=removed,
                replayed=False,
            )


class CompleteWorkout:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: CompleteWorkoutCommand) -> CommandResult[Workout]:
        processed = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=command.user_id,
            operation=CommandOperation.COMPLETE_WORKOUT,
            request_hash=command.request_hash,
            resource_id=command.workout_id,
        )
        async with self._uow_factory() as uow:
            replay = await claim_or_replay(
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
            await uow.workout_log_clarifications.cancel_pending_for_workout(
                command.user_id,
                workout.id,
                terminal_at=datetime.now(timezone.utc),
            )
            await uow.commit()
            return CommandResult(completed, replayed=False)
