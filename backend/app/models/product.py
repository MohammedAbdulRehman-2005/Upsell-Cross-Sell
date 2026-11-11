"""Product and ProductMapping ORM models."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import CatalogueMetadataSource


class Product(TimestampMixin, Base):
    """Represents a product SKU and its explicit catalogue eligibility metadata.

    Nullable `is_rx`, `is_active`, and `available_quantity` fields allow the
    system to represent missing/unknown catalogue metadata and fail closed
    deterministically as required by Stage 1 specifications (T12).
    """

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "available_quantity IS NULL OR available_quantity >= 0",
            name="products_available_quantity_non_negative",
        ),
        CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name="products_unit_price_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_code: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_rx: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True, default=None)
    currency: Mapped[str] = mapped_column(
        String(3),
        default="INR",
        server_default="INR",
        nullable=False,
    )
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    product_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_source: Mapped[CatalogueMetadataSource] = mapped_column(
        String(32),
        default=CatalogueMetadataSource.WORKBOOK_ONLY,
        server_default=CatalogueMetadataSource.WORKBOOK_ONLY.value,
        nullable=False,
        index=True,
    )
    is_synthetic_metadata: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    source_mappings: Mapped[list[ProductMapping]] = relationship(
        "ProductMapping",
        foreign_keys="ProductMapping.source_product_code",
        back_populates="source_product",
        cascade="all, delete-orphan",
        order_by="ProductMapping.recommendation_rank",
    )
    target_mappings: Mapped[list[ProductMapping]] = relationship(
        "ProductMapping",
        foreign_keys="ProductMapping.recommended_product_code",
        back_populates="recommended_product",
    )


class ProductMapping(TimestampMixin, Base):
    """Ranked co-purchase mapping between a source SKU and a target SKU (ranks 1..6)."""

    __tablename__ = "product_mappings"
    __table_args__ = (
        CheckConstraint(
            "recommendation_rank >= 1 AND recommendation_rank <= 6",
            name="product_mappings_rank_between_1_and_6",
        ),
        CheckConstraint(
            "source_product_code <> recommended_product_code",
            name="product_mappings_no_self_mapping",
        ),
        UniqueConstraint(
            "source_product_code",
            "recommendation_rank",
            name="uq_product_mappings_source_rank",
        ),
        UniqueConstraint(
            "source_product_code",
            "recommended_product_code",
            name="uq_product_mappings_source_target",
        ),
        Index("ix_product_mappings_source_code", "source_product_code"),
        Index("ix_product_mappings_target_code", "recommended_product_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_product_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.product_code", ondelete="CASCADE"),
        nullable=False,
    )
    recommended_product_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.product_code", ondelete="RESTRICT"),
        nullable=False,
    )
    recommendation_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_excel_row: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_product: Mapped[Product] = relationship(
        "Product",
        foreign_keys=[source_product_code],
        back_populates="source_mappings",
    )
    recommended_product: Mapped[Product] = relationship(
        "Product",
        foreign_keys=[recommended_product_code],
        back_populates="target_mappings",
    )
