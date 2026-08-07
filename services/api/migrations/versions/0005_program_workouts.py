"""Add versioned programmed workouts.

Revision ID: 0005_program_workouts
Revises: 0004_workout_log_batches
Create Date: 2026-08-06
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_program_workouts"
down_revision: str | None = "0004_workout_log_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "program_workout",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_number", sa.SmallInteger(), nullable=False),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("normalized_alias", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.UniqueConstraint("user_id", "id"),
        sa.CheckConstraint("day_number > 0", name="ck_program_workout_day_number_positive"),
        sa.CheckConstraint("btrim(alias) <> ''", name="ck_program_workout_alias_not_blank"),
        sa.CheckConstraint("btrim(normalized_alias) <> ''", name="ck_program_workout_normalized_alias_not_blank"),
    )
    op.create_index(
        "uq_program_workout_active_day_number", "program_workout", ["user_id", "day_number"],
        unique=True, postgresql_where=sa.text("deactivated_at IS NULL"),
    )
    op.create_index(
        "uq_program_workout_active_alias", "program_workout", ["user_id", "normalized_alias"],
        unique=True, postgresql_where=sa.text("deactivated_at IS NULL"),
    )
    op.create_table(
        "program_workout_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_workout_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("exercise_name", sa.String(255), nullable=False),
        sa.Column("normalized_exercise_name", sa.String(255), nullable=False),
        sa.Column("exercise_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_sets", sa.SmallInteger(), nullable=False),
        sa.Column("target_repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("rest_seconds", sa.Integer()),
        sa.ForeignKeyConstraint(["user_id", "program_workout_id"], ["program_workout.user_id", "program_workout.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["exercise_id"], ["exercise.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("program_workout_id", "position"),
        sa.CheckConstraint("position > 0", name="ck_program_workout_item_position_positive"),
        sa.CheckConstraint("btrim(exercise_name) <> ''", name="ck_program_workout_item_exercise_name_not_blank"),
        sa.CheckConstraint("btrim(normalized_exercise_name) <> ''", name="ck_program_workout_item_normalized_exercise_name_not_blank"),
        sa.CheckConstraint("target_sets > 0", name="ck_program_workout_item_target_sets_positive"),
        sa.CheckConstraint("target_repetitions > 0", name="ck_program_workout_item_target_repetitions_positive"),
        sa.CheckConstraint("rest_seconds IS NULL OR rest_seconds > 0", name="ck_program_workout_item_rest_seconds_positive"),
    )
    op.add_column("workout", sa.Column("program_workout_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_workout_program_workout_id", "workout", "program_workout",
        ["program_workout_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workout_program_workout_id", "workout", type_="foreignkey")
    op.drop_column("workout", "program_workout_id")
    op.drop_table("program_workout_item")
    op.drop_index("uq_program_workout_active_alias", table_name="program_workout")
    op.drop_index("uq_program_workout_active_day_number", table_name="program_workout")
    op.drop_table("program_workout")
