"""Create the initial workout tracking schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale", sa.String(length=16), server_default="it-IT", nullable=False),
        sa.Column(
            "timezone", sa.String(length=64), server_default="Europe/Rome", nullable=False
        ),
        sa.Column(
            "preferred_load_unit", sa.String(length=2), server_default="kg", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("btrim(locale) <> ''", name="ck_app_user_locale_not_blank"),
        sa.CheckConstraint("btrim(timezone) <> ''", name="ck_app_user_timezone_not_blank"),
        sa.CheckConstraint(
            "preferred_load_unit IN ('kg', 'lb')",
            name="ck_app_user_preferred_load_unit_supported",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
    )

    op.create_table(
        "external_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "btrim(provider) <> ''", name="ck_external_identity_provider_not_blank"
        ),
        sa.CheckConstraint(
            "btrim(provider_subject) <> ''",
            name="ck_external_identity_provider_subject_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_external_identity_user_id_app_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_identity"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_external_identity_provider_provider_subject"
        ),
    )

    op.create_table(
        "exercise",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_exercise_name_not_blank"),
        sa.CheckConstraint(
            "btrim(normalized_name) <> ''", name="ck_exercise_normalized_name_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_exercise_user_id_app_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exercise"),
        sa.UniqueConstraint("user_id", "id", name="uq_exercise_user_id_id"),
        sa.UniqueConstraint(
            "user_id", "normalized_name", name="uq_exercise_user_id_normalized_name"
        ),
    )

    op.create_table(
        "workout",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''", name="ck_workout_idempotency_key_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_workout_user_id_app_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workout"),
        sa.UniqueConstraint("idempotency_key", name="uq_workout_idempotency_key"),
        sa.UniqueConstraint("user_id", "id", name="uq_workout_user_id_id"),
    )
    op.create_index(
        "ix_workout_history",
        "workout",
        ["user_id", sa.text("performed_on DESC"), sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "workout_exercise",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workout_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exercise_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("position > 0", name="ck_workout_exercise_position_positive"),
        sa.ForeignKeyConstraint(
            ["user_id", "exercise_id"],
            ["exercise.user_id", "exercise.id"],
            name="fk_workout_exercise_user_id_exercise_id_exercise",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "workout_id"],
            ["workout.user_id", "workout.id"],
            name="fk_workout_exercise_user_id_workout_id_workout",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workout_exercise"),
        sa.UniqueConstraint("user_id", "id", name="uq_workout_exercise_user_id_id"),
        sa.UniqueConstraint(
            "workout_id", "position", name="uq_workout_exercise_workout_id_position"
        ),
    )
    op.create_index(
        "ix_workout_exercise_user_id_exercise_id",
        "workout_exercise",
        ["user_id", "exercise_id"],
        unique=False,
    )

    op.create_table(
        "performed_set",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workout_exercise_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("set_number", sa.SmallInteger(), nullable=False),
        sa.Column("repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("load_value", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("load_unit", sa.String(length=2), nullable=True),
        sa.Column("load_kg", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(load_value IS NULL AND load_unit IS NULL AND load_kg IS NULL) OR "
            "(load_value IS NOT NULL AND load_unit IS NOT NULL AND load_kg IS NOT NULL)",
            name="ck_performed_set_load_fields_all_null_or_present",
        ),
        sa.CheckConstraint(
            "load_kg IS NULL OR load_kg >= 0", name="ck_performed_set_load_kg_non_negative"
        ),
        sa.CheckConstraint(
            "load_unit IS NULL OR load_unit IN ('kg', 'lb')",
            name="ck_performed_set_load_unit_supported",
        ),
        sa.CheckConstraint(
            "load_value IS NULL OR load_value >= 0",
            name="ck_performed_set_load_value_non_negative",
        ),
        sa.CheckConstraint(
            "repetitions > 0", name="ck_performed_set_repetitions_positive"
        ),
        sa.CheckConstraint(
            "set_number > 0", name="ck_performed_set_set_number_positive"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "workout_exercise_id"],
            ["workout_exercise.user_id", "workout_exercise.id"],
            name="fk_performed_set_user_id_workout_exercise_id_workout_exercise",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_performed_set"),
        sa.UniqueConstraint(
            "workout_exercise_id",
            "set_number",
            name="uq_performed_set_workout_exercise_id_set_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("performed_set")
    op.drop_index("ix_workout_exercise_user_id_exercise_id", table_name="workout_exercise")
    op.drop_table("workout_exercise")
    op.drop_index("ix_workout_history", table_name="workout")
    op.drop_table("workout")
    op.drop_table("exercise")
    op.drop_table("external_identity")
    op.drop_table("app_user")
