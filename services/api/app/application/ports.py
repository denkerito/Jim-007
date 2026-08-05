"""Persistence ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.application.commands import (
    ExerciseCatalogItem,
    WorkoutDateInterpretation,
    WorkoutInterpretationContext,
    WorkoutLogInterpretation,
)
from app.domain.models import (
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    User,
    Workout,
    WorkoutExercise,
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
    ) -> Workout: ...

    async def get_by_id(self, workout_id: UUID, user_id: UUID) -> Workout | None: ...

    async def get_active_draft(self, user_id: UUID) -> Workout | None: ...

    async def get_for_update(self, workout_id: UUID, user_id: UUID) -> Workout | None: ...

    async def append_exercise(
        self,
        *,
        workout_id: UUID,
        user_id: UUID,
        occurrence_id: UUID,
        exercise: Exercise,
        notes: str | None,
        sets: tuple[tuple[UUID, int, Load | None, str | None], ...],
    ) -> WorkoutExercise: ...

    async def complete(self, workout_id: UUID, user_id: UUID) -> Workout: ...


class ProcessedCommandRepository(Protocol):
    async def claim(self, command: ProcessedCommand) -> bool: ...

    async def get(self, idempotency_key: str) -> ProcessedCommand | None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    external_identities: ExternalIdentityRepository
    exercises: ExerciseRepository
    workouts: WorkoutRepository
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

    async def interpret_exercises(
        self,
        *,
        text: str,
        context: WorkoutInterpretationContext,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> WorkoutLogInterpretation: ...
