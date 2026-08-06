"""Group workout exercises by the message that logged them.

Revision ID: 0004_workout_log_batches
Revises: 0003_history_indexes
Create Date: 2026-08-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_workout_log_batches"
down_revision: str | None = "0003_history_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workout_exercise",
        sa.Column("log_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute("UPDATE workout_exercise SET log_batch_id = gen_random_uuid()")
    op.alter_column("workout_exercise", "log_batch_id", nullable=False)
    op.create_index(
        "ix_workout_exercise_workout_id_log_batch_id",
        "workout_exercise",
        ["workout_id", "log_batch_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workout_exercise_workout_id_log_batch_id",
        table_name="workout_exercise",
    )
    op.drop_column("workout_exercise", "log_batch_id")
