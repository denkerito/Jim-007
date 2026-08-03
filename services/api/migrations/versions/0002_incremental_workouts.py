"""Support incremental workouts and generic command idempotency.

Revision ID: 0002_incremental_workouts
Revises: 0001_initial_schema
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_incremental_workouts"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workout",
        sa.Column("status", sa.String(length=16), server_default="completed", nullable=False),
    )
    op.add_column(
        "workout",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE workout SET completed_at = created_at")
    op.alter_column("workout", "status", server_default="draft")
    op.create_check_constraint(
        "ck_workout_status_supported",
        "workout",
        "status IN ('draft', 'completed')",
    )
    op.create_check_constraint(
        "ck_workout_status_completed_at_consistent",
        "workout",
        "(status = 'draft' AND completed_at IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL)",
    )
    op.create_index(
        "uq_workout_one_draft_per_user",
        "workout",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    op.create_table(
        "processed_command",
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''",
            name="ck_processed_command_idempotency_key_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(operation) <> ''",
            name="ck_processed_command_operation_not_blank",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_processed_command_request_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_processed_command_user_id_app_user",
        ),
        sa.PrimaryKeyConstraint("idempotency_key", name="pk_processed_command"),
    )

    # Existing keys predate request hashing. Hash the stable persisted representation so
    # they remain replay-detectable without pretending to reconstruct the old HTTP body.
    op.execute(
        """
        INSERT INTO processed_command
            (idempotency_key, user_id, operation, request_hash, resource_id, created_at)
        SELECT
            idempotency_key,
            user_id,
            'legacy_create_workout',
            encode(sha256(convert_to(idempotency_key, 'UTF8')), 'hex'),
            id,
            created_at
        FROM workout
        """
    )
    op.drop_constraint("uq_workout_idempotency_key", "workout", type_="unique")
    op.drop_constraint("ck_workout_idempotency_key_not_blank", "workout", type_="check")
    op.drop_column("workout", "idempotency_key")


def downgrade() -> None:
    op.add_column(
        "workout",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE workout AS w
        SET idempotency_key = pc.idempotency_key
        FROM processed_command AS pc
        WHERE pc.operation IN ('create_workout', 'legacy_create_workout')
          AND pc.resource_id = w.id
        """
    )
    op.execute(
        "UPDATE workout SET idempotency_key = 'downgrade:' || id::text "
        "WHERE idempotency_key IS NULL"
    )
    op.alter_column("workout", "idempotency_key", nullable=False)
    op.create_check_constraint(
        "ck_workout_idempotency_key_not_blank",
        "workout",
        "btrim(idempotency_key) <> ''",
    )
    op.create_unique_constraint(
        "uq_workout_idempotency_key", "workout", ["idempotency_key"]
    )
    op.drop_table("processed_command")
    op.drop_index("uq_workout_one_draft_per_user", table_name="workout")
    op.drop_constraint(
        "ck_workout_status_completed_at_consistent", "workout", type_="check"
    )
    op.drop_constraint("ck_workout_status_supported", "workout", type_="check")
    op.drop_column("workout", "completed_at")
    op.drop_column("workout", "status")
