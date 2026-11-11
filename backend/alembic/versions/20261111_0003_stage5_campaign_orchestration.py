"""Add Stage 5 campaign orchestration and consent audit columns.

Revision ID: 20261111_0003
Revises: 20261111_0002
Create Date: 2026-11-11 23:29:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261111_0003"
down_revision: str | None = "20261111_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Customers: allow nullable phone_number, whatsapp_opt_in, opted_out for fail-closed consent checks,
    # and add consent_source / consent_recorded_at audit columns.
    op.alter_column(
        "customers",
        "phone_number",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "customers",
        "whatsapp_opt_in",
        existing_type=sa.Boolean(),
        server_default=None,
        nullable=True,
    )
    op.alter_column(
        "customers",
        "opted_out",
        existing_type=sa.Boolean(),
        server_default=None,
        nullable=True,
    )
    op.add_column(
        "customers",
        sa.Column("consent_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "customers",
        sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Webhook events: add source and occurred_at columns
    op.add_column(
        "webhook_events",
        sa.Column("source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webhook_events",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Message logs: allow nullable recipient_phone / provider (for suppressed / pre-dispatch records),
    # and add trigger_event_id, recommendation_status, suppression_reason, metadata_provenance.
    op.alter_column(
        "message_logs",
        "recipient_phone",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "message_logs",
        "provider",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.add_column(
        "message_logs",
        sa.Column("trigger_event_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("recommendation_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("suppression_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("metadata_provenance", sa.JSON(), nullable=True),
    )
    op.create_index(
        op.f("ix_message_logs_trigger_event_id"),
        "message_logs",
        ["trigger_event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_logs_suppression_reason"),
        "message_logs",
        ["suppression_reason"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_message_logs_suppression_reason"), table_name="message_logs")
    op.drop_index(op.f("ix_message_logs_trigger_event_id"), table_name="message_logs")
    op.drop_column("message_logs", "metadata_provenance")
    op.drop_column("message_logs", "suppression_reason")
    op.drop_column("message_logs", "recommendation_status")
    op.drop_column("message_logs", "trigger_event_id")
    op.alter_column(
        "message_logs",
        "provider",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "message_logs",
        "recipient_phone",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    op.drop_column("webhook_events", "occurred_at")
    op.drop_column("webhook_events", "source")

    op.drop_column("customers", "consent_recorded_at")
    op.drop_column("customers", "consent_source")
    op.alter_column(
        "customers",
        "opted_out",
        existing_type=sa.Boolean(),
        nullable=False,
    )
    op.alter_column(
        "customers",
        "whatsapp_opt_in",
        existing_type=sa.Boolean(),
        nullable=False,
    )
    op.alter_column(
        "customers",
        "phone_number",
        existing_type=sa.String(length=32),
        nullable=False,
    )
