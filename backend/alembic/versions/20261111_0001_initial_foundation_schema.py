"""Initial Stage 2 foundational database schema.

Revision ID: 20261111_0001
Revises:
Create Date: 2026-11-11 19:20:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261111_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_customer_id", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column(
            "whatsapp_opt_in",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "opted_out",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    op.create_index(
        op.f("ix_customers_external_customer_id"),
        "customers",
        ["external_customer_id"],
        unique=True,
    )
    op.create_index(
        "ix_customers_phone_number",
        "customers",
        ["phone_number"],
        unique=False,
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("is_rx", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column("product_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "available_quantity IS NULL OR available_quantity >= 0",
            name=op.f("ck_products_products_available_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name=op.f("ck_products_products_unit_price_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(
        op.f("ix_products_product_code"),
        "products",
        ["product_code"],
        unique=True,
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_order_id", sa.String(length=64), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            server_default="RECEIVED",
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
    )
    op.create_index(
        op.f("ix_webhook_events_event_id"),
        "webhook_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_webhook_events_event_type"),
        "webhook_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_events_external_order_id"),
        "webhook_events",
        ["external_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_events_order_event_type",
        "webhook_events",
        ["external_order_id", "event_type"],
        unique=False,
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_order_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column(
            "order_status",
            sa.String(length=32),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_orders_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(
        op.f("ix_orders_customer_id"),
        "orders",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_external_order_id"),
        "orders",
        ["external_order_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_orders_order_status"),
        "orders",
        ["order_status"],
        unique=False,
    )
    op.create_index(
        "ix_orders_customer_status",
        "orders",
        ["customer_id", "order_status"],
        unique=False,
    )

    op.create_table(
        "product_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_product_code", sa.String(length=64), nullable=False),
        sa.Column("recommended_product_code", sa.String(length=64), nullable=False),
        sa.Column("recommendation_rank", sa.Integer(), nullable=False),
        sa.Column("source_excel_row", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "recommendation_rank >= 1 AND recommendation_rank <= 6",
            name=op.f("ck_product_mappings_product_mappings_rank_between_1_and_6"),
        ),
        sa.CheckConstraint(
            "source_product_code <> recommended_product_code",
            name=op.f("ck_product_mappings_product_mappings_no_self_mapping"),
        ),
        sa.ForeignKeyConstraint(
            ["recommended_product_code"],
            ["products.product_code"],
            name=op.f("fk_product_mappings_recommended_product_code_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_product_code"],
            ["products.product_code"],
            name=op.f("fk_product_mappings_source_product_code_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_mappings")),
        sa.UniqueConstraint(
            "source_product_code",
            "recommendation_rank",
            name="uq_product_mappings_source_rank",
        ),
        sa.UniqueConstraint(
            "source_product_code",
            "recommended_product_code",
            name="uq_product_mappings_source_target",
        ),
    )
    op.create_index(
        "ix_product_mappings_source_code",
        "product_mappings",
        ["source_product_code"],
        unique=False,
    )
    op.create_index(
        "ix_product_mappings_target_code",
        "product_mappings",
        ["recommended_product_code"],
        unique=False,
    )

    op.create_table(
        "order_line_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "line_index >= 0",
            name=op.f("ck_order_line_items_order_line_items_line_index_non_negative"),
        ),
        sa.CheckConstraint(
            "quantity >= 1",
            name=op.f("ck_order_line_items_order_line_items_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_line_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_line_items")),
        sa.UniqueConstraint(
            "order_id",
            "line_index",
            name="uq_order_line_items_order_line_index",
        ),
    )
    op.create_index(
        "ix_order_line_items_order_id",
        "order_line_items",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_order_line_items_product_code",
        "order_line_items",
        ["product_code"],
        unique=False,
    )

    op.create_table(
        "message_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column(
            "campaign_type",
            sa.String(length=64),
            server_default="POST_DELIVERY_UPSELL",
            nullable=False,
        ),
        sa.Column("recipient_phone", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column(
            "delivery_status",
            sa.String(length=32),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("recommended_skus", sa.JSON(), nullable=False),
        sa.Column("decision_trace", sa.JSON(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("provider_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name=op.f("ck_message_logs_message_logs_retry_count_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_message_logs_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_message_logs_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_logs")),
        sa.UniqueConstraint(
            "order_id",
            "campaign_type",
            name="uq_message_logs_order_campaign",
        ),
    )
    op.create_index(
        op.f("ix_message_logs_customer_id"),
        "message_logs",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_logs_delivery_status",
        "message_logs",
        ["delivery_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_logs_idempotency_key"),
        "message_logs",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_message_logs_order_id"),
        "message_logs",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_logs_provider_message_id",
        "message_logs",
        ["provider_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_message_logs_provider_message_id", table_name="message_logs")
    op.drop_index(op.f("ix_message_logs_order_id"), table_name="message_logs")
    op.drop_index(op.f("ix_message_logs_idempotency_key"), table_name="message_logs")
    op.drop_index("ix_message_logs_delivery_status", table_name="message_logs")
    op.drop_index(op.f("ix_message_logs_customer_id"), table_name="message_logs")
    op.drop_table("message_logs")

    op.drop_index("ix_order_line_items_product_code", table_name="order_line_items")
    op.drop_index("ix_order_line_items_order_id", table_name="order_line_items")
    op.drop_table("order_line_items")

    op.drop_index("ix_product_mappings_target_code", table_name="product_mappings")
    op.drop_index("ix_product_mappings_source_code", table_name="product_mappings")
    op.drop_table("product_mappings")

    op.drop_index("ix_orders_customer_status", table_name="orders")
    op.drop_index(op.f("ix_orders_order_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_external_order_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_webhook_events_order_event_type", table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_external_order_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_type"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_id"), table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index(op.f("ix_products_product_code"), table_name="products")
    op.drop_table("products")

    op.drop_index("ix_customers_phone_number", table_name="customers")
    op.drop_index(op.f("ix_customers_external_customer_id"), table_name="customers")
    op.drop_table("customers")
