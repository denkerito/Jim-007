"""Immutable Pydantic entities and value objects for workout tracking."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Self
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
