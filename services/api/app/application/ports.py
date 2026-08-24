"""Persistence ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.application.commands import (
    ExerciseCatalogItem,
    ExerciseHistoryItem,
    ExerciseQueryInterpretation,
    HistoryCursor,
    WorkoutDateInterpretation,
    WorkoutInterpretationContext,
    WorkoutLogInterpretation,
    WorkoutLogFollowupInterpretation,
    ProgramWorkoutCatalogItem,
    ProgramWorkoutInterpretation,
    WorkoutStartInterpretation,
    ProgramExerciseResolution,
    ProgramExerciseResolutionInput,
)
from app.domain.models import (
    AuthToken,
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    User,
    Workout,
    WorkoutLogClarification,
    WorkoutExercise,
    ProgramWorkout,
    TelegramLinkRequest,
    WebAccount,
    WebSession,
)

if TYPE_CHECKING:
    from app.application.statistics import StatisticsSetRecord


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

    async def get_by_user_provider(
        self, user_id: UUID, provider: str
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

    async def delete(self, identity_id: UUID) -> None: ...


class WebAccountRepository(Protocol):
    async def acquire_email_lock(self, normalized_email: str) -> None: ...

    async def get_by_user_id(self, user_id: UUID) -> WebAccount | None: ...
    async def get_by_normalized_email(self, normalized_email: str) -> WebAccount | None: ...
    async def create(
        self, *, user_id: UUID, email: str, normalized_email: str, password_hash: str
    ) -> WebAccount: ...
    async def verify_email(self, user_id: UUID, verified_at: datetime) -> WebAccount: ...
    async def update_password(self, user_id: UUID, password_hash: str) -> WebAccount: ...
    async def record_login_failure(
        self, user_id: UUID, *, failed_count: int, locked_until: datetime | None
    ) -> None: ...
    async def clear_login_failures(self, user_id: UUID) -> None: ...


class WebSessionRepository(Protocol):
    async def create(
        self, *, session_id: UUID, user_id: UUID, token_hash: str,
        created_at: datetime, expires_at: datetime
    ) -> WebSession: ...
    async def get_active_by_hash(self, token_hash: str, now: datetime) -> WebSession | None: ...
    async def revoke_by_hash(self, token_hash: str, revoked_at: datetime) -> None: ...
    async def revoke_all_for_user(self, user_id: UUID, revoked_at: datetime) -> None: ...


class AuthTokenRepository(Protocol):
    async def revoke_active(self, user_id: UUID, purpose: str, revoked_at: datetime) -> None: ...
    async def create(
        self, *, token_id: UUID, user_id: UUID, purpose: str, token_hash: str,
        created_at: datetime, expires_at: datetime
    ) -> AuthToken: ...
    async def get_for_update(self, token_hash: str) -> AuthToken | None: ...
    async def consume(self, token_id: UUID, consumed_at: datetime) -> None: ...


class TelegramLinkRequestRepository(Protocol):
    async def acquire_user_lock(self, user_id: UUID) -> None: ...
    async def revoke_pending_for_user(self, user_id: UUID, now: datetime) -> None: ...
    async def create(
        self, *, request_id: UUID, user_id: UUID, token_hash: str,
        created_at: datetime, expires_at: datetime
    ) -> TelegramLinkRequest: ...
    async def get_by_id_for_user(self, request_id: UUID, user_id: UUID) -> TelegramLinkRequest | None: ...
    async def get_by_id_for_update(self, request_id: UUID, user_id: UUID) -> TelegramLinkRequest | None: ...
    async def get_by_hash_for_update(self, token_hash: str) -> TelegramLinkRequest | None: ...
    async def set_candidate(
        self, request_id: UUID, *, telegram_user_id: str,
        username: str | None, display_name: str | None
    ) -> TelegramLinkRequest: ...
    async def complete(self, request_id: UUID, completed_at: datetime) -> TelegramLinkRequest: ...
    async def cancel(self, request_id: UUID, cancelled_at: datetime) -> None: ...


class ExerciseRepository(Protocol):
    async def list_for_user(self, user_id: UUID) -> tuple[Exercise, ...]: ...

    async def get_by_id(self, exercise_id: UUID, user_id: UUID) -> Exercise | None: ...

    async def get_by_normalized_name(
        self, user_id: UUID, normalized_name: str
    ) -> Exercise | None: ...

    async def get_or_create(
        self, *, exercise_id: UUID, user_id: UUID, name: str, normalized_name: str
    ) -> tuple[Exercise, bool]: ...


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


class StatisticsRepository(Protocol):
    async def list_completed_sets(
        self,
        user_id: UUID,
        *,
        from_date: date | None,
        to_date: date,
        exercise_id: UUID | None = None,
    ) -> tuple["StatisticsSetRecord", ...]: ...


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


class WorkoutLogClarificationRepository(Protocol):
    async def create(
        self,
        *,
        clarification_id: UUID,
        user_id: UUID,
        workout_id: UUID,
        original_text: str,
        clarification_message: str,
        model: str,
        initial_prompt_version: str,
        followup_prompt_version: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> WorkoutLogClarification: ...

    async def get_by_id(
        self, clarification_id: UUID, user_id: UUID
    ) -> WorkoutLogClarification | None: ...

    async def get_for_update(
        self, clarification_id: UUID, user_id: UUID
    ) -> WorkoutLogClarification | None: ...

    async def get_pending_for_workout(
        self, user_id: UUID, workout_id: UUID
    ) -> WorkoutLogClarification | None: ...

    async def finish(
        self, clarification_id: UUID, user_id: UUID, *, status: str, terminal_at: datetime
    ) -> WorkoutLogClarification: ...

    async def cancel_pending_for_workout(
        self, user_id: UUID, workout_id: UUID, *, terminal_at: datetime
    ) -> int: ...


class UnitOfWork(Protocol):
    users: UserRepository
    external_identities: ExternalIdentityRepository
    web_accounts: WebAccountRepository
    web_sessions: WebSessionRepository
    auth_tokens: AuthTokenRepository
    telegram_link_requests: TelegramLinkRequestRepository
    exercises: ExerciseRepository
    workouts: WorkoutRepository
    statistics: StatisticsRepository
    program_workouts: ProgramWorkoutRepository
    processed_commands: ProcessedCommandRepository
    workout_log_clarifications: WorkoutLogClarificationRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


class WorkoutTextInterpreter(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def workout_log_prompt_version(self) -> str: ...

    @property
    def workout_log_followup_prompt_version(self) -> str: ...

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

    async def interpret_exercise_followup(
        self,
        *,
        original_text: str,
        clarification_message: str,
        answer_text: str,
        context: WorkoutInterpretationContext,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> WorkoutLogFollowupInterpretation: ...


class ExerciseQueryInterpreter(Protocol):
    async def resolve_exercise(
        self,
        *,
        text: str,
        locale: str,
        catalog: tuple[ExerciseCatalogItem, ...],
    ) -> ExerciseQueryInterpretation: ...
