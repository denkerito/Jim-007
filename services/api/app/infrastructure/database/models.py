from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("btrim(locale) <> ''", name="locale_not_blank"),
        CheckConstraint("btrim(timezone) <> ''", name="timezone_not_blank"),
        CheckConstraint(
            "preferred_load_unit IN ('kg', 'lb')",
            name="preferred_load_unit_supported",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'it-IT'")
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Europe/Rome'")
    )
    preferred_load_unit: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'kg'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ExternalIdentity(Base):
    __tablename__ = "external_identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject"),
        CheckConstraint("btrim(provider) <> ''", name="provider_not_blank"),
        CheckConstraint(
            "btrim(provider_subject) <> ''", name="provider_subject_not_blank"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Exercise(Base):
    __tablename__ = "exercise"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_name"),
        UniqueConstraint("user_id", "id"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint(
            "btrim(normalized_name) <> ''", name="normalized_name_not_blank"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Workout(Base):
    __tablename__ = "workout"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        CheckConstraint("status IN ('draft', 'completed')", name="status_supported"),
        CheckConstraint(
            "(status = 'draft' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL)",
            name="status_completed_at_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    performed_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'draft'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="WorkoutExercise.position",
    )


Index(
    "ix_workout_history",
    Workout.user_id,
    Workout.status,
    Workout.performed_on.desc(),
    Workout.created_at.desc(),
    Workout.id.desc(),
)

Index(
    "uq_workout_one_draft_per_user",
    Workout.user_id,
    unique=True,
    postgresql_where=Workout.status == "draft",
)


class WorkoutExercise(Base):
    __tablename__ = "workout_exercise"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "workout_id"],
            ["workout.user_id", "workout.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "exercise_id"],
            ["exercise.user_id", "exercise.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workout_id", "position"),
        UniqueConstraint("user_id", "id"),
        CheckConstraint("position > 0", name="position_positive"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    workout_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    exercise_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    workout: Mapped[Workout] = relationship(
        back_populates="exercises", lazy="raise", overlaps="exercise"
    )
    exercise: Mapped[Exercise] = relationship(
        lazy="raise", overlaps="exercises,workout"
    )
    sets: Mapped[list["PerformedSet"]] = relationship(
        back_populates="workout_exercise",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="PerformedSet.set_number",
    )


Index(
    "ix_workout_exercise_user_id_exercise_id",
    WorkoutExercise.user_id,
    WorkoutExercise.exercise_id,
    WorkoutExercise.workout_id,
)


class PerformedSet(Base):
    __tablename__ = "performed_set"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "workout_exercise_id"],
            ["workout_exercise.user_id", "workout_exercise.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workout_exercise_id", "set_number"),
        CheckConstraint("set_number > 0", name="set_number_positive"),
        CheckConstraint("repetitions > 0", name="repetitions_positive"),
        CheckConstraint(
            "load_unit IS NULL OR load_unit IN ('kg', 'lb')",
            name="load_unit_supported",
        ),
        CheckConstraint(
            "load_value IS NULL OR load_value >= 0", name="load_value_non_negative"
        ),
        CheckConstraint("load_kg IS NULL OR load_kg >= 0", name="load_kg_non_negative"),
        CheckConstraint(
            "(load_value IS NULL AND load_unit IS NULL AND load_kg IS NULL) OR "
            "(load_value IS NOT NULL AND load_unit IS NOT NULL AND load_kg IS NOT NULL)",
            name="load_fields_all_null_or_present",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    workout_exercise_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    set_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    repetitions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    load_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    load_unit: Mapped[str | None] = mapped_column(String(2))
    load_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    notes: Mapped[str | None] = mapped_column(Text)
    workout_exercise: Mapped[WorkoutExercise] = relationship(
        back_populates="sets", lazy="raise"
    )


class ProcessedCommand(Base):
    __tablename__ = "processed_command"
    __table_args__ = (
        CheckConstraint("btrim(idempotency_key) <> ''", name="idempotency_key_not_blank"),
        CheckConstraint("btrim(operation) <> ''", name="operation_not_blank"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_sha256"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
