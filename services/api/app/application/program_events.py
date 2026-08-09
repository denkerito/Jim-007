"""Application orchestration for versioned programmed workouts."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.application.commands import (
    ExerciseCatalogItem,
    InterpretationStatus,
    ProcessProgramEventCommand,
    ProgramEventAction,
    ProgramEventResult,
    WorkoutInterpretationContext,
)
from app.application.idempotency import CommandOperation, verify_replay
from app.application.ports import ProcessedCommand, UnitOfWorkFactory, WorkoutTextInterpreter
from app.domain.exceptions import (
    TelegramNotLinkedError,
    NotFoundError,
    ProgramWorkoutConflictError,
    LlmInvalidResponseError,
)
from app.domain.normalization import clean_required_text, normalize_exercise_name


class ProcessProgramEvent:
    def __init__(self, uow_factory: UnitOfWorkFactory, interpreter: WorkoutTextInterpreter) -> None:
        self._uow_factory = uow_factory
        self._interpreter = interpreter

    async def execute(self, command: ProcessProgramEventCommand) -> ProgramEventResult:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject(
                command.provider.strip(), command.provider_subject.strip()
            )
            if identity is None:
                raise TelegramNotLinkedError("Telegram is not linked to a web account")
            user = await uow.users.get_by_id(identity.user_id)
            if user is None:
                raise RuntimeError("External identity references a missing user")
            existing = await uow.processed_commands.get(command.idempotency_key)
            if existing is not None:
                operation = self._operation(command.action)
                verify_replay(existing, ProcessedCommand(
                    idempotency_key=command.idempotency_key, user_id=user.id,
                    operation=operation, request_hash=command.request_hash,
                    resource_id=existing.resource_id,
                ))
                if command.action is ProgramEventAction.NEW:
                    return ProgramEventResult(kind="reset", replayed=True)
                program = await uow.program_workouts.get_by_id(existing.resource_id, user.id)
                if program is None:
                    raise NotFoundError("The previously processed program workout no longer exists")
                return ProgramEventResult(
                    kind="created" if command.action is ProgramEventAction.CREATE else "edited",
                    program_workout=program, replayed=True,
                )
            if command.action is ProgramEventAction.NEW:
                return await self._reset(command, user.id)
            catalog = await uow.exercises.list_for_user(user.id)

        context = WorkoutInterpretationContext(
            locale=user.locale,
            timezone=user.timezone,
            current_date=datetime.now(ZoneInfo(user.timezone)).date(),
            preferred_load_unit=user.preferred_load_unit,
        )
        interpretation = await self._interpreter.interpret_program(
            text=(command.text or "").strip(), context=context,
            catalog=tuple(ExerciseCatalogItem(id=item.id, name=item.name) for item in catalog),
        )
        if interpretation.status is InterpretationStatus.NEEDS_CLARIFICATION:
            return ProgramEventResult(
                kind="needs_clarification",
                clarification_message=interpretation.clarification_message,
            )

        program_id = uuid4()
        operation = self._operation(command.action)
        async with self._uow_factory() as uow:
            await uow.program_workouts.acquire_user_lock(user.id)
            if await uow.processed_commands.claim(ProcessedCommand(
                idempotency_key=command.idempotency_key, user_id=user.id,
                operation=operation, request_hash=command.request_hash,
                resource_id=program_id,
            )) is False:
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise ProgramWorkoutConflictError("Idempotency claim disappeared")
                verify_replay(existing, ProcessedCommand(
                    idempotency_key=command.idempotency_key, user_id=user.id,
                    operation=operation, request_hash=command.request_hash,
                    resource_id=existing.resource_id,
                ))
                program = await uow.program_workouts.get_by_id(existing.resource_id, user.id)
                if program is None:
                    raise NotFoundError("The previously processed program workout no longer exists")
                return ProgramEventResult(
                    kind="created" if command.action is ProgramEventAction.CREATE else "edited",
                    program_workout=program, replayed=True,
                )

            if command.action is ProgramEventAction.CREATE:
                day_number = command.day_number
                alias = clean_required_text(command.alias or "")
                if alias.isdecimal():
                    raise ProgramWorkoutConflictError("Program alias must not be numeric")
                active = await uow.program_workouts.list_active(user.id)
                normalized_alias = normalize_exercise_name(alias)
                if any(item.day_number == day_number or item.normalized_alias == normalized_alias for item in active):
                    raise ProgramWorkoutConflictError("An active programmed workout already uses this number or alias")
            else:
                selector = clean_required_text(command.selector or "")
                normalized_selector = selector if selector.isdecimal() else normalize_exercise_name(selector)
                previous = await uow.program_workouts.get_active_by_selector(user.id, normalized_selector)
                if previous is None:
                    raise NotFoundError("Active programmed workout not found")
                day_number = previous.day_number
                alias = previous.alias
                normalized_alias = previous.normalized_alias
                await uow.program_workouts.deactivate(previous.id, user.id)

            catalog_by_id = {item.id: item for item in await uow.exercises.list_for_user(user.id)}
            catalog_by_name = {item.normalized_name: item for item in catalog_by_id.values()}
            rows = []
            for item in interpretation.exercises:
                if item.target_sets is None or item.target_repetitions is None:
                    raise LlmInvalidResponseError("Ready program contains an incomplete exercise")
                name = clean_required_text(item.name)
                normalized_name = normalize_exercise_name(name)
                exercise_id = item.catalog_exercise_id
                if exercise_id is not None and exercise_id not in catalog_by_id:
                    raise LlmInvalidResponseError("LLM returned an exercise outside the user catalog")
                if exercise_id is None and normalized_name in catalog_by_name:
                    exercise_id = catalog_by_name[normalized_name].id
                rows.append((
                    uuid4(), name, normalized_name, exercise_id,
                    item.target_sets, item.target_repetitions, item.rest_seconds,
                ))
            program = await uow.program_workouts.create(
                program_workout_id=program_id, user_id=user.id,
                day_number=int(day_number), alias=alias,
                normalized_alias=normalized_alias, notes=command.notes,
                items=tuple(rows),
            )
            await uow.commit()
            return ProgramEventResult(
                kind="created" if command.action is ProgramEventAction.CREATE else "edited",
                program_workout=program,
            )

    async def _reset(self, command: ProcessProgramEventCommand, user_id) -> ProgramEventResult:
        async with self._uow_factory() as uow:
            await uow.program_workouts.acquire_user_lock(user_id)
            requested = ProcessedCommand(
                idempotency_key=command.idempotency_key, user_id=user_id,
                operation=CommandOperation.RESET_PROGRAM, request_hash=command.request_hash,
                resource_id=user_id,
            )
            if not await uow.processed_commands.claim(requested):
                existing = await uow.processed_commands.get(command.idempotency_key)
                if existing is None:
                    raise ProgramWorkoutConflictError("Idempotency claim disappeared")
                verify_replay(existing, requested)
                return ProgramEventResult(kind="reset", replayed=True)
            count = await uow.program_workouts.deactivate_all(user_id)
            await uow.commit()
            return ProgramEventResult(kind="reset", deactivated_count=count)

    @staticmethod
    def _operation(action: ProgramEventAction) -> CommandOperation:
        return {
            ProgramEventAction.NEW: CommandOperation.RESET_PROGRAM,
            ProgramEventAction.CREATE: CommandOperation.CREATE_PROGRAM_WORKOUT,
            ProgramEventAction.EDIT: CommandOperation.EDIT_PROGRAM_WORKOUT,
        }[action]
