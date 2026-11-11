"""Add catalogue metadata provenance columns to products.

Revision ID: 20261111_0002
Revises: 20261111_0001
Create Date: 2026-11-11 20:14:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261111_0002"
down_revision: str | None = "20261111_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "metadata_source",
            sa.String(length=32),
            server_default="WORKBOOK_ONLY",
            nullable=False,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "is_synthetic_metadata",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_products_metadata_source"),
        "products",
        ["metadata_source"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_products_metadata_source"), table_name="products")
    op.drop_column("products", "is_synthetic_metadata")
    op.drop_column("products", "metadata_source")
