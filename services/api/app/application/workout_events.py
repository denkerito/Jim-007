"""Provider-neutral orchestration for workout events received from chat adapters."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.application.commands import (
    CancelWorkoutCommand,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
    ExerciseCatalogItem,
    InterpretationStatus,
    FollowupInterpretationStatus,
    LogWorkoutMessageCommand,
    ProcessWorkoutEventCommand,
    UndoWorkoutMessageCommand,
    WorkoutEventAction,
    WorkoutEventResult,
    WorkoutInterpretationContext,
    ProgramWorkoutCatalogItem,
    ProgramExerciseHistory,
    ProgramExerciseResolutionInput,
)
from app.application.idempotency import CommandOperation, verify_replay
from app.application.ports import (
    ProcessedCommand,
    UnitOfWork,
    UnitOfWorkFactory,
    WorkoutTextInterpreter,
)
from app.application.services import (
    CancelWorkout,
    CompleteWorkout,
    CreateWorkout,
    LogWorkoutMessage,
    UndoWorkoutMessage,
)
from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    TelegramNotLinkedError,
    NoActiveWorkoutError,
    NotFoundError,
    LlmInvalidResponseError,
    IdempotencyConflictError,
)
from app.domain.models import Exercise, ProgramWorkout, User, Workout, WorkoutStatus
from app.domain.models import WorkoutLogClarification, WorkoutLogClarificationStatus


REWRITE_REQUIRED_MESSAGE = (
    "Non riesco ancora a interpretarlo. Riscrivi l'intero esercizio."
)


class ProcessWorkoutEvent:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        interpreter: WorkoutTextInterpreter,
        *,
        clarification_ttl_seconds: int = 900,
    ) -> None:
        self._uow_factory = uow_factory
        self._interpreter = interpreter
        self._clarification_ttl = timedelta(seconds=clarification_ttl_seconds)

    async def execute(self, command: ProcessWorkoutEventCommand) -> WorkoutEventResult:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject(
                command.provider.strip(), command.provider_subject.strip()
            )
            if identity is None:
                raise TelegramNotLinkedError(
                    "Telegram is not linked to a web account"
                )
            user = await uow.users.get_by_id(identity.user_id)
            if user is None:
                raise RuntimeError("External identity references a missing user")
            active = await uow.workouts.get_active_draft(user.id)
            existing = await uow.processed_commands.get(command.idempotency_key)
            if existing is not None:
                return await self._replay(uow, command, user, existing)
            catalog_values = (
                await uow.exercises.list_for_user(user.id)
                if command.action is WorkoutEventAction.LOG
                else ()
            )
            active_programs = (
                await uow.program_workouts.list_active(user.id)
                if command.action is WorkoutEventAction.OPEN
                else ()
            )

            if command.action is WorkoutEventAction.LOG:
                if active is None:
                    raise NoActiveWorkoutError("Open a workout with /workout first")
                pending_clarification = (
                    await uow.workout_log_clarifications.get_pending_for_workout(
                        user.id, active.id
                    )
                )
                if (
                    pending_clarification is not None
                    and pending_clarification.expires_at <= datetime.now(timezone.utc)
                ):
                    await uow.workout_log_clarifications.finish(
                        pending_clarification.id,
                        user.id,
                        status=WorkoutLogClarificationStatus.EXPIRED.value,
                        terminal_at=datetime.now(timezone.utc),
                    )
                    await uow.commit()
                    pending_clarification = None
            else:
                pending_clarification = None
        context = WorkoutInterpretationContext(
            locale=user.locale,
            timezone=user.timezone,
            current_date=datetime.now(ZoneInfo(user.timezone)).date(),
            preferred_load_unit=user.preferred_load_unit,
        )

        if command.action is WorkoutEventAction.OPEN:
            return await self._open(command, user, active, context, active_programs)

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

        return await self._log(
            command,
            user,
            active,
            context,
            catalog_values,
            pending_clarification,
        )

    async def _replay(
        self,
        uow: UnitOfWork,
        command: ProcessWorkoutEventCommand,
        user: User,
        existing: ProcessedCommand,
    ) -> WorkoutEventResult:
        clarification_operations = {
            CommandOperation.REQUEST_WORKOUT_LOG_CLARIFICATION,
            CommandOperation.REQUIRE_WORKOUT_LOG_REWRITE,
        }
        if existing.operation in clarification_operations:
            if command.action is not WorkoutEventAction.LOG:
                requested_operation = CommandOperation.LOG_WORKOUT_MESSAGE
            else:
                requested_operation = existing.operation
            verify_replay(
                existing,
                ProcessedCommand(
                    idempotency_key=command.idempotency_key,
                    user_id=user.id,
                    operation=requested_operation,
                    request_hash=command.request_hash,
                    resource_id=existing.resource_id,
                ),
            )
            clarification = await uow.workout_log_clarifications.get_by_id(
                existing.resource_id, user.id
            )
            if clarification is None:
                raise NotFoundError(
                    "The previously processed clarification no longer exists"
                )
            if (
                existing.operation
                == CommandOperation.REQUEST_WORKOUT_LOG_CLARIFICATION
                and clarification.status is WorkoutLogClarificationStatus.PENDING
            ):
                if clarification.expires_at <= datetime.now(timezone.utc):
                    await uow.workout_log_clarifications.finish(
                        clarification.id,
                        user.id,
                        status=WorkoutLogClarificationStatus.EXPIRED.value,
                        terminal_at=datetime.now(timezone.utc),
                    )
                    await uow.commit()
                    return WorkoutEventResult(
                        kind="rewrite_required",
                        clarification_message=REWRITE_REQUIRED_MESSAGE,
                        replayed=True,
                    )
                return WorkoutEventResult(
                    kind="needs_clarification",
                    clarification_message=clarification.clarification_message,
                    replayed=True,
                )
            if clarification.status is WorkoutLogClarificationStatus.RESOLVED:
                workout = await uow.workouts.get_by_id(
                    clarification.workout_id, user.id
                )
                if workout is None:
                    raise NotFoundError(
                        "The previously processed workout no longer exists"
                    )
                return WorkoutEventResult(
                    kind="logged", workout=workout, replayed=True
                )
            return WorkoutEventResult(
                kind="rewrite_required",
                clarification_message=REWRITE_REQUIRED_MESSAGE,
                replayed=True,
            )

        operations = {
            WorkoutEventAction.OPEN: CommandOperation.CREATE_WORKOUT,
            WorkoutEventAction.LOG: CommandOperation.LOG_WORKOUT_MESSAGE,
            WorkoutEventAction.COMPLETE: CommandOperation.COMPLETE_WORKOUT,
            WorkoutEventAction.CANCEL: CommandOperation.CANCEL_WORKOUT,
            WorkoutEventAction.UNDO: CommandOperation.UNDO_WORKOUT_MESSAGE,
        }
        verify_replay(
            existing,
            ProcessedCommand(
                idempotency_key=command.idempotency_key,
                user_id=user.id,
                operation=operations[command.action],
                request_hash=command.request_hash,
                resource_id=existing.resource_id,
            ),
        )
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

    async def _open(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        active: Workout | None,
        context: WorkoutInterpretationContext,
        active_programs,
    ) -> WorkoutEventResult:
        if active is not None:
            raise ActiveWorkoutExistsError(active.id)
        text = (command.text or "").strip()
        if text:
            if hasattr(self._interpreter, "interpret_start"):
                interpretation = await self._interpreter.interpret_start(
                    text=text, context=context,
                    programs=tuple(
                        ProgramWorkoutCatalogItem(
                            id=item.id, day_number=item.day_number, alias=item.alias
                        ) for item in active_programs
                    ),
                )
            else:  # Compatibility for provider adapters implementing the original port.
                legacy = await self._interpreter.interpret_date(text=text, context=context)
                interpretation = legacy
            if interpretation.status is InterpretationStatus.NEEDS_CLARIFICATION:
                return WorkoutEventResult(
                    kind="needs_clarification",
                    clarification_message=interpretation.clarification_message,
                )
            if getattr(interpretation, "kind", "date") == "program":
                program = next(
                    (item for item in active_programs if item.id == interpretation.program_workout_id),
                    None,
                )
                if program is None:
                    raise NotFoundError("The interpreted programmed workout is not active")
                performed_on = context.current_date
                notes = program.notes
            else:
                program = None
                performed_on = interpretation.performed_on
                notes = interpretation.notes
        else:
            program = None
            performed_on = context.current_date
            notes = None
        program_history = (
            await self._program_history(user, program)
            if program is not None
            else ()
        )
        result = await CreateWorkout(self._uow_factory).execute(
            CreateWorkoutCommand(
                user_id=user.id,
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash,
                performed_on=performed_on,
                notes=notes,
                program_workout_id=program.id if program is not None else None,
            )
        )
        return WorkoutEventResult(
            kind="opened",
            workout=result.value,
            replayed=result.replayed,
            program_history=program_history,
        )

    async def _program_history(
        self, user: User, program: ProgramWorkout
    ) -> tuple[ProgramExerciseHistory, ...]:
        async with self._uow_factory() as uow:
            catalog = await uow.exercises.list_for_user(user.id)
        catalog_ids = {item.id for item in catalog}
        by_name = {item.normalized_name: item.id for item in catalog}
        resolved = {
            item.id: item.exercise_id or by_name.get(item.normalized_exercise_name)
            for item in program.items
        }
        unresolved = tuple(
            ProgramExerciseResolutionInput(item_id=item.id, name=item.exercise_name)
            for item in program.items
            if resolved[item.id] is None
        )
        if unresolved and catalog and hasattr(self._interpreter, "resolve_program_exercises"):
            llm_resolution = await self._interpreter.resolve_program_exercises(
                items=unresolved, locale=user.locale,
                catalog=tuple(ExerciseCatalogItem(id=item.id, name=item.name) for item in catalog),
            )
            expected_items = {item.item_id for item in unresolved}
            returned_items = {item.item_id for item in llm_resolution.resolutions}
            if returned_items != expected_items or any(
                item.exercise_id is not None and item.exercise_id not in catalog_ids
                for item in llm_resolution.resolutions
            ):
                raise LlmInvalidResponseError("LLM returned an invalid program exercise resolution")
            resolved.update({item.item_id: item.exercise_id for item in llm_resolution.resolutions})
        exercise_ids = tuple(dict.fromkeys(
            exercise_id for exercise_id in resolved.values() if exercise_id is not None
        ))
        async with self._uow_factory() as uow:
            latest = await uow.workouts.latest_completed_for_exercises(user.id, exercise_ids)
        return tuple(
            ProgramExerciseHistory(item=item, latest=latest.get(resolved[item.id]))
            for item in program.items
        )

    async def _log(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        active: Workout | None,
        context: WorkoutInterpretationContext,
        catalog_values: tuple[Exercise, ...],
        pending_clarification: WorkoutLogClarification | None,
    ) -> WorkoutEventResult:
        if active is None:  # Defensive: execute checked this before the LLM call.
            raise NoActiveWorkoutError("Open a workout with /workout first")
        catalog = tuple(
            ExerciseCatalogItem(id=exercise.id, name=exercise.name)
            for exercise in catalog_values
        )
        text = (command.text or "").strip()
        if pending_clarification is not None:
            if (
                pending_clarification.original_text is None
                or pending_clarification.clarification_message is None
            ):
                raise RuntimeError("Pending clarification is missing its transcript")
            followup = await self._interpreter.interpret_exercise_followup(
                original_text=pending_clarification.original_text,
                clarification_message=pending_clarification.clarification_message,
                answer_text=text,
                context=context,
                catalog=catalog,
            )
            if followup.status is FollowupInterpretationStatus.REWRITE_REQUIRED:
                return await self._require_rewrite(
                    command, user, pending_clarification
                )
            exercises = followup.exercises
            clarification_id = pending_clarification.id
        else:
            interpretation = await self._interpreter.interpret_exercises(
                text=text,
                context=context,
                catalog=catalog,
            )
            if interpretation.status is InterpretationStatus.NEEDS_CLARIFICATION:
                return await self._create_clarification(
                    command,
                    user,
                    active,
                    text,
                    interpretation.clarification_message
                    or "Quali dati mancano per completare l'esercizio?",
                )
            exercises = interpretation.exercises
            clarification_id = None

        result = await LogWorkoutMessage(self._uow_factory).execute(
            LogWorkoutMessageCommand(
                user_id=user.id,
                workout_id=active.id,
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash,
                exercises=exercises,
                clarification_id=clarification_id,
            )
        )
        return WorkoutEventResult(
            kind="logged",
            workout=result.workout,
            added_exercises=result.added_exercises,
            replayed=result.replayed,
        )

    async def _create_clarification(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        active: Workout,
        original_text: str,
        clarification_message: str,
    ) -> WorkoutEventResult:
        clarification_id = uuid4()
        created_at = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            workout = await uow.workouts.get_for_update(active.id, user.id)
            if workout is None or workout.status is not WorkoutStatus.DRAFT:
                raise NoActiveWorkoutError("Open a workout with /workout first")
            clarification = await uow.workout_log_clarifications.create(
                clarification_id=clarification_id,
                user_id=user.id,
                workout_id=workout.id,
                original_text=original_text,
                clarification_message=clarification_message,
                model=getattr(self._interpreter, "model_name", "unknown"),
                initial_prompt_version=getattr(
                    self._interpreter, "workout_log_prompt_version", "workout-log-v2"
                ),
                followup_prompt_version=getattr(
                    self._interpreter,
                    "workout_log_followup_prompt_version",
                    "workout-log-followup-v1",
                ),
                created_at=created_at,
                expires_at=created_at + self._clarification_ttl,
            )
            requested = ProcessedCommand(
                idempotency_key=command.idempotency_key,
                user_id=user.id,
                operation=CommandOperation.REQUEST_WORKOUT_LOG_CLARIFICATION,
                request_hash=command.request_hash,
                resource_id=clarification.id,
            )
            if clarification.id != clarification_id:
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise IdempotencyConflictError(
                        "Another workout clarification is already pending"
                    )
                return await self._replay(uow, command, user, existing)
            if not await uow.processed_commands.claim(requested):
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise IdempotencyConflictError(
                        "Idempotency claim disappeared unexpectedly"
                    )
                return await self._replay(uow, command, user, existing)
            await uow.commit()
        return WorkoutEventResult(
            kind="needs_clarification",
            clarification_message=clarification_message,
        )

    async def _require_rewrite(
        self,
        command: ProcessWorkoutEventCommand,
        user: User,
        clarification: WorkoutLogClarification,
    ) -> WorkoutEventResult:
        requested = ProcessedCommand(
            idempotency_key=command.idempotency_key,
            user_id=user.id,
            operation=CommandOperation.REQUIRE_WORKOUT_LOG_REWRITE,
            request_hash=command.request_hash,
            resource_id=clarification.id,
        )
        async with self._uow_factory() as uow:
            if not await uow.processed_commands.claim(requested):
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise IdempotencyConflictError(
                        "Idempotency claim disappeared unexpectedly"
                    )
                return await self._replay(uow, command, user, existing)
            locked = await uow.workout_log_clarifications.get_for_update(
                clarification.id, user.id
            )
            if locked is None:
                raise NotFoundError("Workout clarification not found")
            if locked.status is not WorkoutLogClarificationStatus.PENDING:
                raise IdempotencyConflictError(
                    "The workout clarification is no longer pending"
                )
            await uow.workout_log_clarifications.finish(
                locked.id,
                user.id,
                status=WorkoutLogClarificationStatus.REWRITE_REQUIRED.value,
                terminal_at=datetime.now(timezone.utc),
            )
            await uow.commit()
        return WorkoutEventResult(
            kind="rewrite_required",
            clarification_message=REWRITE_REQUIRED_MESSAGE,
        )
