"""Provider-neutral orchestration for workout events received from chat adapters."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.application.commands import (
    CancelWorkoutCommand,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
    ExerciseCatalogItem,
    InterpretationStatus,
    LogWorkoutMessageCommand,
    ProcessWorkoutEventCommand,
    UndoWorkoutMessageCommand,
    WorkoutEventAction,
    WorkoutEventResult,
    WorkoutInterpretationContext,
)
from app.application.idempotency import CommandOperation, verify_replay
from app.application.ports import ProcessedCommand, UnitOfWorkFactory, WorkoutTextInterpreter
from app.application.services import (
    CancelWorkout,
    CompleteWorkout,
    CreateWorkout,
    LogWorkoutMessage,
    UndoWorkoutMessage,
)
from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    ExternalIdentityNotRegisteredError,
    NoActiveWorkoutError,
    NotFoundError,
)
from app.domain.models import Exercise, User, Workout


class ProcessWorkoutEvent:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        interpreter: WorkoutTextInterpreter,
    ) -> None:
        self._uow_factory = uow_factory
        self._interpreter = interpreter

    async def execute(self, command: ProcessWorkoutEventCommand) -> WorkoutEventResult:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject(
                command.provider.strip(), command.provider_subject.strip()
            )
            if identity is None:
                raise ExternalIdentityNotRegisteredError(
                    "External identity is not registered"
                )
            user = await uow.users.get_by_id(identity.user_id)
            if user is None:
                raise RuntimeError("External identity references a missing user")
            active = await uow.workouts.get_active_draft(user.id)
            existing = await uow.processed_commands.get(command.idempotency_key)
            if existing is not None:
                operations = {
                    WorkoutEventAction.OPEN: CommandOperation.CREATE_WORKOUT,
                    WorkoutEventAction.LOG: CommandOperation.LOG_WORKOUT_MESSAGE,
                    WorkoutEventAction.COMPLETE: CommandOperation.COMPLETE_WORKOUT,
                    WorkoutEventAction.CANCEL: CommandOperation.CANCEL_WORKOUT,
                    WorkoutEventAction.UNDO: CommandOperation.UNDO_WORKOUT_MESSAGE,
                }
                requested = ProcessedCommand(
                    idempotency_key=command.idempotency_key,
                    user_id=user.id,
                    operation=operations[command.action],
                    request_hash=command.request_hash,
                    resource_id=existing.resource_id,
                )
                verify_replay(existing, requested)
                if command.action is WorkoutEventAction.CANCEL:
                    return WorkoutEventResult(kind="cancelled", replayed=True)
                replayed_workout = await uow.workouts.get_by_id(
                    existing.resource_id, user.id
                )
                if replayed_workout is None:
                    raise NotFoundError("The previously processed workout no longer exists")
                kinds: dict[
                    WorkoutEventAction,
                    Literal["opened", "logged", "completed", "undone"],
                ] = {
                    WorkoutEventAction.OPEN: "opened",
                    WorkoutEventAction.LOG: "logged",
                    WorkoutEventAction.COMPLETE: "completed",
                    WorkoutEventAction.UNDO: "undone",
                }
                return WorkoutEventResult(
                    kind=kinds[command.action],
                    workout=replayed_workout,
                    replayed=True,
                )
            catalog_values = (
                await uow.exercises.list_for_user(user.id)
                if command.action is WorkoutEventAction.LOG
                else ()
            )

            if command.action is WorkoutEventAction.LOG:
                if active is None:
                    raise NoActiveWorkoutError("Open a workout with /workout first")
        context = WorkoutInterpretationContext(
            locale=user.locale,
            timezone=user.timezone,
            current_date=datetime.now(ZoneInfo(user.timezone)).date(),
            preferred_load_unit=user.preferred_load_unit,
        )

        if command.action is WorkoutEventAction.OPEN:
            return await self._open(command, user, active, context)

        if command.action is WorkoutEventAction.COMPLETE:
            if active is None:
                raise NoActiveWorkoutError("There is no active workout to complete")
            result = await CompleteWorkout(self._uow_factory).execute(
                CompleteWorkoutCommand(
                    user_id=user.id,
                    workout_id=active.id,
                    idempotency_key=command.idempotency_key,
                    request_hash=command.request_hash,
                )
            )
            return WorkoutEventResult(
                kind="completed",
                workout=result.value,
                replayed=result.replayed,
            )

        if command.action is WorkoutEventAction.CANCEL:
            if active is None:
                raise NoActiveWorkoutError("There is no active workout to cancel")
            result = await CancelWorkout(self._uow_factory).execute(
                CancelWorkoutCommand(
                    user_id=user.id,
                    workout_id=active.id,
                    idempotency_key=command.idempotency_key,
                    request_hash=command.request_hash,
                )
            )
            return WorkoutEventResult(kind="cancelled", replayed=result.replayed)

        if command.action is WorkoutEventAction.UNDO:
            if active is None:
                raise NoActiveWorkoutError("There is no active workout to update")
            result = await UndoWorkoutMessage(self._uow_factory).execute(
                UndoWorkoutMessageCommand(
                    user_id=user.id,
                    workout_id=active.id,
                    idempotency_key=command.idempotency_key,
                    request_hash=command.request_hash,
                )
            )
            return WorkoutEventResult(
                kind="undone",
                workout=result.workout,
                removed_exercises=result.removed_exercises,
                replayed=result.replayed,
            )

        return await self._log(command, user, active, context, catalog_values)

    async def _open(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        active: Workout | None,
        context: WorkoutInterpretationContext,
    ) -> WorkoutEventResult:
        if active is not None:
            raise ActiveWorkoutExistsError(active.id)
        text = (command.text or "").strip()
        if text:
            interpretation = await self._interpreter.interpret_date(
                text=text,
                context=context,
            )
            if interpretation.status is InterpretationStatus.NEEDS_CLARIFICATION:
                return WorkoutEventResult(
                    kind="needs_clarification",
                    clarification_message=interpretation.clarification_message,
                )
            performed_on = interpretation.performed_on
            notes = interpretation.notes
        else:
            performed_on = context.current_date
            notes = None
        result = await CreateWorkout(self._uow_factory).execute(
            CreateWorkoutCommand(
                user_id=user.id,
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash,
                performed_on=performed_on,
                notes=notes,
            )
        )
        return WorkoutEventResult(
            kind="opened",
            workout=result.value,
            replayed=result.replayed,
        )

    async def _log(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        active: Workout | None,
        context: WorkoutInterpretationContext,
        catalog_values: tuple[Exercise, ...],
    ) -> WorkoutEventResult:
        if active is None:  # Defensive: execute checked this before the LLM call.
            raise NoActiveWorkoutError("Open a workout with /workout first")
        interpretation = await self._interpreter.interpret_exercises(
            text=(command.text or "").strip(),
            context=context,
            catalog=tuple(
                ExerciseCatalogItem(id=exercise.id, name=exercise.name)
                for exercise in catalog_values
            ),
        )
        if interpretation.status is InterpretationStatus.NEEDS_CLARIFICATION:
            return WorkoutEventResult(
                kind="needs_clarification",
                clarification_message=interpretation.clarification_message,
            )
        result = await LogWorkoutMessage(self._uow_factory).execute(
            LogWorkoutMessageCommand(
                user_id=user.id,
                workout_id=active.id,
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash,
                exercises=interpretation.exercises,
            )
        )
        return WorkoutEventResult(
            kind="logged",
            workout=result.workout,
            added_exercises=result.added_exercises,
            replayed=result.replayed,
        )
