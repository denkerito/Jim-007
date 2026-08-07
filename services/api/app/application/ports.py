"""Persistence ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.application.commands import (
    ExerciseCatalogItem,
    ExerciseHistoryItem,
    ExerciseQueryInterpretation,
    HistoryCursor,
    WorkoutDateInterpretation,
    WorkoutInterpretationContext,
    WorkoutLogInterpretation,
    ProgramWorkoutCatalogItem,
    ProgramWorkoutInterpretation,
    WorkoutStartInterpretation,
    ProgramExerciseResolution,
    ProgramExerciseResolutionInput,
)
from app.domain.models import (
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    User,
    Workout,
    WorkoutExercise,
    ProgramWorkout,
)


@dataclass(frozen=True, slots=True)
class ProcessedCommand:
    idempotency_key: str
    user_id: UUID
    operation: str
    request_hash: str
    resource_id: UUID


class UserRepository(Protocol):
    async def get_by_id(self, user_id: UUID) -> User | None: ...

    async def create(
        self,
        *,
        user_id: UUID,
        locale: str,
        timezone: str,
        preferred_load_unit: LoadUnit,
    ) -> User: ...


class ExternalIdentityRepository(Protocol):
    async def acquire_registration_lock(
        self, provider: str, provider_subject: str
    ) -> None: ...

    async def get_by_provider_subject(
        self, provider: str, provider_subject: str
    ) -> ExternalIdentity | None: ...

    async def create(
        self,
        *,
        identity_id: UUID,
        user_id: UUID,
        provider: str,
        provider_subject: str,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity: ...

    async def update_profile(
        self,
        identity_id: UUID,
        *,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity: ...


class ExerciseRepository(Protocol):
    async def list_for_user(self, user_id: UUID) -> tuple[Exercise, ...]: ...

    async def get_by_id(self, exercise_id: UUID, user_id: UUID) -> Exercise | None: ...

    async def get_by_normalized_name(
        self, user_id: UUID, normalized_name: str
    ) -> Exercise | None: ...

    async def get_or_create(
        self, *, exercise_id: UUID, user_id: UUID, name: str, normalized_name: str
    ) -> Exercise: ...


class WorkoutRepository(Protocol):
    async def create(
        self,
        *,
        workout_id: UUID,
        user_id: UUID,
        performed_on: date,
        notes: str | None,
        program_workout_id: UUID | None = None,
    ) -> Workout: ...

    async def get_by_id(self, workout_id: UUID, user_id: UUID) -> Workout | None: ...

    async def get_active_draft(self, user_id: UUID) -> Workout | None: ...

    async def get_for_update(self, workout_id: UUID, user_id: UUID) -> Workout | None: ...

    async def append_exercise(
        self,
        *,
        workout_id: UUID,
        user_id: UUID,
        log_batch_id: UUID,
        occurrence_id: UUID,
        exercise: Exercise,
        notes: str | None,
        sets: tuple[tuple[UUID, int, Load | None, str | None], ...],
    ) -> WorkoutExercise: ...

    async def delete(self, workout_id: UUID, user_id: UUID) -> None: ...

    async def delete_last_log_batch(
        self, workout_id: UUID, user_id: UUID
    ) -> tuple[WorkoutExercise, ...]: ...

    async def complete(self, workout_id: UUID, user_id: UUID) -> Workout: ...

    async def list_completed(
        self,
        user_id: UUID,
        *,
        limit: int,
        after: HistoryCursor | None,
    ) -> tuple[Workout, ...]: ...

    async def list_completed_for_exercise(
        self,
        user_id: UUID,
        exercise_id: UUID,
        *,
        limit: int,
        after: HistoryCursor | None,
    ) -> tuple[ExerciseHistoryItem, ...]: ...

    async def latest_completed_for_exercises(
        self, user_id: UUID, exercise_ids: tuple[UUID, ...]
    ) -> dict[UUID, ExerciseHistoryItem]: ...


class ProgramWorkoutRepository(Protocol):
    async def acquire_user_lock(self, user_id: UUID) -> None: ...
    async def list_active(self, user_id: UUID) -> tuple[ProgramWorkout, ...]: ...
    async def get_by_id(self, program_workout_id: UUID, user_id: UUID) -> ProgramWorkout | None: ...
    async def get_active_by_selector(self, user_id: UUID, selector: str) -> ProgramWorkout | None: ...
    async def deactivate_all(self, user_id: UUID) -> int: ...
    async def deactivate(self, program_workout_id: UUID, user_id: UUID) -> None: ...
    async def create(
        self, *, program_workout_id: UUID, user_id: UUID, day_number: int,
        alias: str, normalized_alias: str, notes: str | None,
        items: tuple[tuple[UUID, str, str, UUID | None, int, int, int | None], ...],
    ) -> ProgramWorkout: ...


class ProcessedCommandRepository(Protocol):
    async def claim(self, command: ProcessedCommand) -> bool: ...

    async def get(self, idempotency_key: str) -> ProcessedCommand | None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    external_identities: ExternalIdentityRepository
    exercises: ExerciseRepository
    workouts: WorkoutRepository
    program_workouts: ProgramWorkoutRepository
    processed_commands: ProcessedCommandRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


class WorkoutTextInterpreter(Protocol):
    async def interpret_date(
        self,
        *,
        text: str,
        context: WorkoutInterpretationContext,
    ) -> WorkoutDateInterpretation: ...

    async def interpret_start(
        self, *, text: str, context: WorkoutInterpretationContext,
        programs: tuple[ProgramWorkoutCatalogItem, ...],
    ) -> WorkoutStartInterpretation: ...

    async def interpret_program(
        self, *, text: str, context: WorkoutInterpretationContext,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> ProgramWorkoutInterpretation: ...

    async def resolve_program_exercises(
        self, *, items: tuple[ProgramExerciseResolutionInput, ...],
        locale: str, catalog: tuple[ExerciseCatalogItem, ...],
    ) -> ProgramExerciseResolution: ...

    async def interpret_exercises(
        self,
        *,
        text: str,
        context: WorkoutInterpretationContext,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> WorkoutLogInterpretation: ...


class ExerciseQueryInterpreter(Protocol):
    async def resolve_exercise(
        self,
        *,
        text: str,
        locale: str,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> ExerciseQueryInterpretation: ...
