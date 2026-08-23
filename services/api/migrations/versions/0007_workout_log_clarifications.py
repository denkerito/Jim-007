"""Add single-turn workout log clarifications.

Revision ID: 0007_workout_log_clarifications
Revises: 0006_web_auth_telegram_linking
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_workout_log_clarifications"
down_revision: str | None = "0006_web_auth_telegram_linking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workout_log_clarification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workout_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_text", sa.Text()),
        sa.Column("clarification_message", sa.Text()),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("initial_prompt_version", sa.String(64), nullable=False),
        sa.Column("followup_prompt_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'rewrite_required', 'cancelled', 'expired')",
            name="ck_workout_log_clarification_status_supported",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_workout_log_clarification_expires_after_creation",
        ),
        sa.CheckConstraint(
            "btrim(model) <> ''",
            name="ck_workout_log_clarification_model_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(initial_prompt_version) <> ''",
            name="ck_workout_log_clarification_initial_prompt_version_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(followup_prompt_version) <> ''",
            name="ck_workout_log_clarification_followup_prompt_version_not_blank",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND original_text IS NOT NULL "
            "AND btrim(original_text) <> '' AND clarification_message IS NOT NULL "
            "AND btrim(clarification_message) <> '' AND terminal_at IS NULL) OR "
            "(status <> 'pending' AND original_text IS NULL "
            "AND clarification_message IS NULL AND terminal_at IS NOT NULL)",
            name="ck_workout_log_clarification_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "workout_id"],
            ["workout.user_id", "workout.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_workout_log_clarification_pending",
        "workout_log_clarification",
        ["user_id", "workout_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_workout_log_clarification_workout_status",
        "workout_log_clarification",
        ["user_id", "workout_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workout_log_clarification_workout_status",
        table_name="workout_log_clarification",
    )
    op.drop_index(
        "uq_workout_log_clarification_pending",
        table_name="workout_log_clarification",
    )
    op.drop_table("workout_log_clarification")
