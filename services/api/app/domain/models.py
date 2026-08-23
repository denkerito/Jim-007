"""Immutable Pydantic entities and value objects for workout tracking."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.domain.exceptions import InvalidWorkoutStateError, WorkoutNotEditableError
from app.domain.normalization import clean_required_text, normalize_exercise_name


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LoadUnit(StrEnum):
    KG = "kg"
    LB = "lb"


class WorkoutStatus(StrEnum):
    DRAFT = "draft"
    COMPLETED = "completed"


class WorkoutLogClarificationStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REWRITE_REQUIRED = "rewrite_required"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ProgramWorkoutItem(DomainModel):
    id: UUID
    position: int = Field(gt=0, le=32767)
    exercise_name: str
    normalized_exercise_name: str
    exercise_id: UUID | None = None
    target_sets: int = Field(gt=0, le=32767)
    target_repetitions: int = Field(gt=0, le=32767)
    rest_seconds: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def names_must_be_normalized(self) -> Self:
        cleaned = clean_required_text(self.exercise_name)
        if not cleaned or cleaned != self.exercise_name:
            raise ValueError("program exercise name must be clean and non-blank")
        if self.normalized_exercise_name != normalize_exercise_name(self.exercise_name):
            raise ValueError("normalized program exercise name does not match")
        return self


class ProgramWorkout(DomainModel):
    id: UUID
    user_id: UUID
    day_number: int = Field(gt=0, le=32767)
    alias: str
    normalized_alias: str
    notes: str | None = None
    created_at: datetime
    deactivated_at: datetime | None = None
    items: tuple[ProgramWorkoutItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identity_and_positions_must_be_valid(self) -> Self:
        cleaned = clean_required_text(self.alias)
        if not cleaned or cleaned != self.alias or cleaned.isdecimal():
            raise ValueError("program alias must be clean, non-numeric and non-blank")
        if self.normalized_alias != normalize_exercise_name(self.alias):
            raise ValueError("normalized program alias does not match")
        if tuple(item.position for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("program item positions must be consecutive")
        return self


class User(DomainModel):
    id: UUID
    locale: str
    timezone: str
    preferred_load_unit: LoadUnit
    created_at: datetime
    updated_at: datetime

    @field_validator("locale", "timezone")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        cleaned = clean_required_text(value)
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("must be a valid IANA timezone") from error
        return value


class ExternalIdentity(DomainModel):
    id: UUID
    user_id: UUID
    provider: str
    provider_subject: str
    username: str | None = None
    display_name: str | None = None
    created_at: datetime

    @field_validator("provider", "provider_subject")
    @classmethod
    def identity_text_must_not_be_blank(cls, value: str) -> str:
        cleaned = clean_required_text(value)
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class WebAccount(DomainModel):
    user_id: UUID
    email: str
    normalized_email: str
    password_hash: str
    email_verified_at: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WebSession(DomainModel):
    id: UUID
    user_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


class AuthToken(DomainModel):
    id: UUID
    user_id: UUID
    purpose: Literal["verify_email", "reset_password"]
    token_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class TelegramLinkRequest(DomainModel):
    id: UUID
    user_id: UUID
    token_hash: str
    status: Literal[
        "pending_telegram", "pending_web_confirmation", "completed", "cancelled"
    ]
    candidate_telegram_user_id: str | None = None
    candidate_username: str | None = None
    candidate_display_name: str | None = None
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    @model_validator(mode="after")
    def state_must_be_consistent(self) -> Self:
        if self.status == "pending_telegram":
            valid = (
                self.candidate_telegram_user_id is None
                and self.completed_at is None
                and self.cancelled_at is None
            )
        elif self.status == "pending_web_confirmation":
            valid = (
                self.candidate_telegram_user_id is not None
                and self.completed_at is None
                and self.cancelled_at is None
            )
        elif self.status == "completed":
            valid = (
                self.candidate_telegram_user_id is not None
                and self.completed_at is not None
                and self.cancelled_at is None
            )
        else:
            valid = self.completed_at is None and self.cancelled_at is not None
        if not valid:
            raise ValueError("Telegram link request state is inconsistent")
        return self


class Exercise(DomainModel):
    id: UUID
    user_id: UUID
    name: str
    normalized_name: str
    created_at: datetime

    @model_validator(mode="after")
    def normalized_name_must_match(self) -> Self:
        cleaned_name = clean_required_text(self.name)
        if not cleaned_name:
            raise ValueError("exercise name must not be blank")
        if self.name != cleaned_name:
            raise ValueError("exercise name must be normalized for display")
        if self.normalized_name != normalize_exercise_name(self.name):
            raise ValueError("normalized exercise name does not match name")
        return self


class Load(DomainModel):
    value: Decimal = Field(ge=0, max_digits=10, decimal_places=3)
    unit: LoadUnit

    @computed_field
    @property
    def kilograms(self) -> Decimal:
        factor = Decimal("1") if self.unit is LoadUnit.KG else Decimal("0.45359237")
        return (self.value * factor).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


class PerformedSet(DomainModel):
    id: UUID
    set_number: int = Field(gt=0, le=32767)
    repetitions: int = Field(gt=0, le=32767)
    load: Load | None = None
    notes: str | None = None


class WorkoutExercise(DomainModel):
    id: UUID
    exercise: Exercise
    position: int = Field(gt=0, le=32767)
    notes: str | None = None
    sets: tuple[PerformedSet, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def set_numbers_must_be_consecutive(self) -> Self:
        if tuple(item.set_number for item in self.sets) != tuple(range(1, len(self.sets) + 1)):
            raise ValueError("set numbers must be consecutive and start from one")
        return self


class Workout(DomainModel):
    id: UUID
    user_id: UUID
    performed_on: date
    status: WorkoutStatus
    notes: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    program_workout: ProgramWorkout | None = None
    exercises: tuple[WorkoutExercise, ...] = ()

    @model_validator(mode="after")
    def state_must_be_consistent(self) -> Self:
        if self.status is WorkoutStatus.DRAFT and self.completed_at is not None:
            raise ValueError("a draft workout cannot have completed_at")
        if self.status is WorkoutStatus.COMPLETED:
            if self.completed_at is None:
                raise ValueError("a completed workout requires completed_at")
            if not self.exercises:
                raise ValueError("a completed workout requires at least one exercise")
        expected = tuple(range(1, len(self.exercises) + 1))
        if tuple(item.position for item in self.exercises) != expected:
            raise ValueError("exercise positions must be consecutive and start from one")
        if any(item.exercise.user_id != self.user_id for item in self.exercises):
            raise ValueError("all exercises must be owned by the workout user")
        return self

    def with_exercise(self, exercise: WorkoutExercise) -> Workout:
        if self.status is not WorkoutStatus.DRAFT:
            raise WorkoutNotEditableError("A completed workout cannot accept exercises")
        return Workout(**{**self.model_dump(exclude={"exercises"}), "exercises": (*self.exercises, exercise)})

    def as_completed(self, completed_at: datetime) -> Workout:
        if self.status is WorkoutStatus.COMPLETED:
            return self
        if not self.exercises:
            raise InvalidWorkoutStateError("A workout without exercises cannot be completed")
        return Workout(
            **{
                **self.model_dump(exclude={"status", "completed_at", "exercises"}),
                "status": WorkoutStatus.COMPLETED,
                "completed_at": completed_at,
                "exercises": self.exercises,
            }
        )


class WorkoutLogClarification(DomainModel):
    id: UUID
    user_id: UUID
    workout_id: UUID
    status: WorkoutLogClarificationStatus
    original_text: str | None = None
    clarification_message: str | None = None
    model: str
    initial_prompt_version: str
    followup_prompt_version: str
    created_at: datetime
    expires_at: datetime
    terminal_at: datetime | None = None

    @model_validator(mode="after")
    def state_must_be_consistent(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("clarification must expire after creation")
        for value in (
            self.model,
            self.initial_prompt_version,
            self.followup_prompt_version,
        ):
            if not clean_required_text(value):
                raise ValueError("clarification metadata must not be blank")
        if self.status is WorkoutLogClarificationStatus.PENDING:
            if (
                not clean_required_text(self.original_text or "")
                or not clean_required_text(self.clarification_message or "")
                or self.terminal_at is not None
            ):
                raise ValueError("pending clarification state is inconsistent")
        elif (
            self.original_text is not None
            or self.clarification_message is not None
            or self.terminal_at is None
        ):
            raise ValueError("terminal clarification state is inconsistent")
        return self
