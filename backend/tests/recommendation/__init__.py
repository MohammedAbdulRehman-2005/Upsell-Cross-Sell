"""Stage 4 recommendation engine test package."""
from __future__ import annotations

from decimal import Decimal

from app.services.recommendation import (
    CatalogueItemInput,
    MappingCandidateInput,
)


def make_catalogue_item(
    code: str,
    *,
    name: str | None = None,
    is_rx: bool | None = False,
    is_active: bool | None = True,
    available_quantity: int | None = 25,
    unit_price: Decimal | None = Decimal("299.00"),
) -> CatalogueItemInput:
    """Create an explicit `CatalogueItemInput` for pure unit tests."""
    return CatalogueItemInput(
        product_code=code,
        product_name=name or f"Product {code}",
        is_rx=is_rx,
        is_active=is_active,
        available_quantity=available_quantity,
        unit_price=unit_price,
        currency="INR",
        metadata_source="SYNTHETIC_FIXTURE",
        is_synthetic_metadata=True,
    )


def make_six_mappings(
    source_code: str,
    target_codes: list[str],
) -> list[MappingCandidateInput]:
    """Create 6 ranked `MappingCandidateInput` entries (ranks 1..6) for a source SKU."""
    assert len(target_codes) == 6
    return [
        MappingCandidateInput(
            source_product_code=source_code,
            recommended_product_code=tgt,
            recommendation_rank=rank,
            recommended_product_name=f"Product {tgt}",
        )
        for rank, tgt in enumerate(target_codes, start=1)
    ]


def build_independent_clusters_fixture(
    num_sources: int = 8,
) -> tuple[dict[str, CatalogueItemInput], list[MappingCandidateInput]]:
    """Build `num_sources` independent source products `S1..Sn`, each mapping to 6 unique targets `S{i}-T1..T6`."""
    catalogue: dict[str, CatalogueItemInput] = {}
    mappings: list[MappingCandidateInput] = []
    for i in range(1, num_sources + 1):
        src_code = f"SRC-{i:02d}"
        catalogue[src_code] = make_catalogue_item(src_code)
        targets: list[str] = []
        for rank in range(1, 7):
            tgt_code = f"SRC-{i:02d}-T{rank}"
            catalogue[tgt_code] = make_catalogue_item(tgt_code)
            targets.append(tgt_code)
        mappings.extend(make_six_mappings(src_code, targets))
    return catalogue, mappings
