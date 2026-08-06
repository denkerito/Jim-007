"""Optimize completed workout and exercise history queries.

Revision ID: 0003_history_indexes
Revises: 0002_incremental_workouts
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_history_indexes"
down_revision: str | None = "0002_incremental_workouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_workout_history", table_name="workout")
    op.create_index(
        "ix_workout_history",
        "workout",
        [
            "user_id",
            "status",
            sa.text("performed_on DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )
    op.drop_index(
        "ix_workout_exercise_user_id_exercise_id",
        table_name="workout_exercise",
    )
    op.create_index(
        "ix_workout_exercise_user_id_exercise_id",
        "workout_exercise",
        ["user_id", "exercise_id", "workout_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workout_exercise_user_id_exercise_id",
        table_name="workout_exercise",
    )
    op.create_index(
        "ix_workout_exercise_user_id_exercise_id",
        "workout_exercise",
        ["user_id", "exercise_id"],
        unique=False,
    )
    op.drop_index("ix_workout_history", table_name="workout")
    op.create_index(
        "ix_workout_history",
        "workout",
        ["user_id", sa.text("performed_on DESC"), sa.text("created_at DESC")],
        unique=False,
    )
