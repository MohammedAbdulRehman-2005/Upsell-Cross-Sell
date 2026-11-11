"""Add Stage 6 WhatsApp provider dispatch columns and simulator_messages table.

Revision ID: 20261111_0004
Revises: 20261111_0003
Create Date: 2026-11-12 00:34:30

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261111_0004"
down_revision: str | None = "20261111_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add Stage 6 provider error and lifecycle timestamp columns to message_logs
    op.add_column(
        "message_logs",
        sa.Column("provider_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("provider_error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "message_logs",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Create simulator_messages table for interactive customer conversation tracking
    op.create_table(
        "simulator_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("simulated_message_id", sa.String(length=128), nullable=False),
        sa.Column("message_log_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column(
            "message_type",
            sa.String(length=64),
            server_default="TEMPLATE_RECOMMENDATION",
            nullable=False,
        ),
        sa.Column("is_simulated", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("recipient_phone", sa.String(length=32), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("recommendations_payload", sa.JSON(), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=True),
        sa.Column("selected_product_code", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_log_id"], ["message_logs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simulator_messages_simulated_message_id"),
        "simulator_messages",
        ["simulated_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_simulator_messages_message_log_id"),
        "simulator_messages",
        ["message_log_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_simulator_messages_order_id"),
        "simulator_messages",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_simulator_messages_customer_id"),
        "simulator_messages",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_simulator_messages_campaign_chronological",
        "simulator_messages",
        ["message_log_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_simulator_messages_campaign_chronological", table_name="simulator_messages")
    op.drop_index(op.f("ix_simulator_messages_customer_id"), table_name="simulator_messages")
    op.drop_index(op.f("ix_simulator_messages_order_id"), table_name="simulator_messages")
    op.drop_index(op.f("ix_simulator_messages_message_log_id"), table_name="simulator_messages")
    op.drop_index(op.f("ix_simulator_messages_simulated_message_id"), table_name="simulator_messages")
    op.drop_table("simulator_messages")

    op.drop_column("message_logs", "read_at")
    op.drop_column("message_logs", "dispatch_started_at")
    op.drop_column("message_logs", "provider_error_message")
    op.drop_column("message_logs", "provider_error_code")
