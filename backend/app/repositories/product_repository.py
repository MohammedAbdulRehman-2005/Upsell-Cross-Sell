"""Product and ProductMapping repositories for clean database access."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Product, ProductMapping
from app.schemas.product import ProductCreate, ProductMappingCreate, ProductUpdate


class ProductRepository:
    """Data access layer for Product and ProductMapping records.

    Does not implement recommendation selection, quota allocation, or filtering.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, product_id: int) -> Product | None:
        return self.session.get(Product, product_id)

    def get_by_code(self, product_code: str) -> Product | None:
        stmt = (
            select(Product)
            .where(Product.product_code == product_code)
            .options(selectinload(Product.source_mappings))
        )
        return self.session.scalar(stmt)

    def get_by_codes(self, product_codes: Sequence[str]) -> list[Product]:
        if not product_codes:
            return []
        stmt = (
            select(Product)
            .where(Product.product_code.in_(list(product_codes)))
            .order_by(Product.product_code)
        )
        return list(self.session.scalars(stmt).all())

    def list_all(self, limit: int = 200, offset: int = 0) -> list[Product]:
        stmt = select(Product).order_by(Product.product_code).offset(offset).limit(limit)
        return list(self.session.scalars(stmt).all())

    def get_mapping(
        self,
        source_product_code: str,
        recommendation_rank: int,
    ) -> ProductMapping | None:
        stmt = select(ProductMapping).where(
            ProductMapping.source_product_code == source_product_code,
            ProductMapping.recommendation_rank == recommendation_rank,
        )
        return self.session.scalar(stmt)

    def get_mapping_by_pair(
        self,
        source_product_code: str,
        recommended_product_code: str,
    ) -> ProductMapping | None:
        stmt = select(ProductMapping).where(
            ProductMapping.source_product_code == source_product_code,
            ProductMapping.recommended_product_code == recommended_product_code,
        )
        return self.session.scalar(stmt)

    def list_all_mappings(self) -> list[ProductMapping]:
        stmt = select(ProductMapping).order_by(
            ProductMapping.source_product_code.asc(),
            ProductMapping.recommendation_rank.asc(),
        )
        return list(self.session.scalars(stmt).all())

    def create(self, payload: ProductCreate, *, commit: bool = True) -> Product:
        product = Product(**payload.model_dump())
        self.session.add(product)
        if commit:
            self.session.commit()
            self.session.refresh(product)
        else:
            self.session.flush()
        return product

    def update(self, product: Product, payload: ProductUpdate, *, commit: bool = True) -> Product:
        updates = payload.model_dump(exclude_unset=True)
        for field_name, value in updates.items():
            setattr(product, field_name, value)
        self.session.add(product)
        if commit:
            self.session.commit()
            self.session.refresh(product)
        else:
            self.session.flush()
        return product

    def create_mapping(self, payload: ProductMappingCreate, *, commit: bool = True) -> ProductMapping:
        mapping = ProductMapping(**payload.model_dump())
        self.session.add(mapping)
        if commit:
            self.session.commit()
            self.session.refresh(mapping)
        else:
            self.session.flush()
        return mapping

    def list_mappings_for_source(self, source_product_code: str) -> list[ProductMapping]:
        stmt = (
            select(ProductMapping)
            .where(ProductMapping.source_product_code == source_product_code)
            .order_by(ProductMapping.recommendation_rank.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_mappings_for_sources(self, source_product_codes: Sequence[str]) -> list[ProductMapping]:
        if not source_product_codes:
            return []
        stmt = (
            select(ProductMapping)
            .where(ProductMapping.source_product_code.in_(list(source_product_codes)))
            .order_by(
                ProductMapping.source_product_code.asc(),
                ProductMapping.recommendation_rank.asc(),
            )
        )
        return list(self.session.scalars(stmt).all())
