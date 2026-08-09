"""Add web authentication and Telegram account linking.

Revision ID: 0006_web_auth_telegram_linking
Revises: 0005_program_workouts
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_web_auth_telegram_linking"
down_revision: str | None = "0005_program_workouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_account",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("failed_login_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("btrim(email) <> ''", name="ck_web_account_email_not_blank"),
        sa.CheckConstraint("btrim(normalized_email) <> ''", name="ck_web_account_normalized_email_not_blank"),
        sa.CheckConstraint("failed_login_count >= 0", name="ck_web_account_failed_login_count_non_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("normalized_email"),
    )
    op.create_table(
        "web_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_web_session_token_hash_sha256"),
        sa.CheckConstraint("expires_at > created_at", name="ck_web_session_expires_after_creation"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "auth_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')", name="ck_auth_token_purpose_supported"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_auth_token_token_hash_sha256"),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_token_expires_after_creation"),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_auth_token_terminal_state_exclusive",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "telegram_link_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("candidate_telegram_user_id", sa.String(255)),
        sa.Column("candidate_username", sa.String(255)),
        sa.Column("candidate_display_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending_telegram', 'pending_web_confirmation', 'completed', 'cancelled')",
            name="ck_telegram_link_request_status_supported",
        ),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_telegram_link_request_token_hash_sha256"),
        sa.CheckConstraint("expires_at > created_at", name="ck_telegram_link_request_expires_after_creation"),
        sa.CheckConstraint(
            "(status = 'pending_telegram' AND candidate_telegram_user_id IS NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'pending_web_confirmation' AND candidate_telegram_user_id IS NOT NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND candidate_telegram_user_id IS NOT NULL "
            "AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_telegram_link_request_state_consistent",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_telegram_link_request_user_created", "telegram_link_request", ["user_id", "created_at"]
    )
    op.create_index(
        "uq_telegram_link_request_user_pending",
        "telegram_link_request",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_telegram', 'pending_web_confirmation')"
        ),
    )
    op.create_index(
        "uq_external_identity_user_telegram",
        "external_identity",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("provider = 'telegram'"),
    )


def downgrade() -> None:
    op.drop_index("uq_external_identity_user_telegram", table_name="external_identity")
    op.drop_index("uq_telegram_link_request_user_pending", table_name="telegram_link_request")
    op.drop_index("ix_telegram_link_request_user_created", table_name="telegram_link_request")
    op.drop_table("telegram_link_request")
    op.drop_table("auth_token")
    op.drop_table("web_session")
    op.drop_table("web_account")
