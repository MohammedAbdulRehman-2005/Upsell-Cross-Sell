"""Stage 8: Storefront demo delivery events and inbound WhatsApp messages.

Revision ID: 20261111_0005
Revises: 20261111_0004
Create Date: 2026-11-12 12:45:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261111_0005"
down_revision: str | None = "20261111_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add Stage 8 storefront fields to orders
    op.add_column(
        "orders",
        sa.Column(
            "order_source",
            sa.String(length=32),
            server_default="STOREFRONT",
            nullable=False,
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "is_demo_order",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "delivery_address",
            sa.String(length=512),
            nullable=True,
        ),
    )

    # 2. Add opt_out_recorded_at to customers
    op.add_column(
        "customers",
        sa.Column(
            "opt_out_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 3. Create demo_delivery_events table
    op.create_table(
        "demo_delivery_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="SCHEDULED",
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_delivery_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("step_timestamps", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_demo_delivery_events_order_id"),
        "demo_delivery_events",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_demo_delivery_events_target_delivery_at"),
        "demo_delivery_events",
        ["target_delivery_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_demo_delivery_events_status"),
        "demo_delivery_events",
        ["status"],
        unique=False,
    )

    # 4. Create whatsapp_messages table
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default="META_CLOUD_API",
            nullable=False,
        ),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column(
            "message_type",
            sa.String(length=32),
            server_default="text",
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "is_simulated",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_messages_provider_msg_id"),
        "whatsapp_messages",
        ["provider_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_phone_number"),
        "whatsapp_messages",
        ["phone_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_customer_id"),
        "whatsapp_messages",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_direction"),
        "whatsapp_messages",
        ["direction"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_whatsapp_messages_direction"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_customer_id"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_phone_number"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_provider_msg_id"), table_name="whatsapp_messages")
    op.drop_table("whatsapp_messages")

    op.drop_index(op.f("ix_demo_delivery_events_status"), table_name="demo_delivery_events")
    op.drop_index(op.f("ix_demo_delivery_events_target_delivery_at"), table_name="demo_delivery_events")
    op.drop_index(op.f("ix_demo_delivery_events_order_id"), table_name="demo_delivery_events")
    op.drop_table("demo_delivery_events")

    op.drop_column("customers", "opt_out_recorded_at")

    op.drop_column("orders", "delivery_address")
    op.drop_column("orders", "is_demo_order")
    op.drop_column("orders", "order_source")
