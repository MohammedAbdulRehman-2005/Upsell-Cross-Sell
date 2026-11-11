"""Stage 3 tests for mapping data integrity and negative validation cases."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.services.catalogue_loader import (
    CatalogueDataValidationError,
    CatalogueLoaderService,
    SourceMappingEntry,
    SourceProductRecord,
    validate_dataset_records,
)


def _build_valid_cluster(prefix: str = "CLUSTER") -> list[SourceProductRecord]:
    """Build a valid 7-product isolated mapping cluster (7 products, 42 mappings)."""
    codes = [f"{prefix}-{i:03d}" for i in range(1, 8)]
    names = {c: f"Product {c}" for c in codes}
    records: list[SourceProductRecord] = []
    for idx, source_code in enumerate(codes):
        other_codes = [c for c in codes if c != source_code]
        mappings = tuple(
            SourceMappingEntry(
                source_product_code=source_code,
                recommended_product_code=target_code,
                recommended_product_name=names[target_code],
                recommendation_rank=rank,
                source_excel_row=idx + 2,
            )
            for rank, target_code in enumerate(other_codes, start=1)
        )
        records.append(
            SourceProductRecord(
                product_code=source_code,
                product_name=names[source_code],
                source_excel_row=idx + 2,
                mappings=mappings,
            )
        )
    return records


def test_missing_source_product_detection() -> None:
    """Test 4: Detect when a source product code is missing from the known catalogue."""
    records = _build_valid_cluster()
    incomplete_catalogue = {r.product_code for r in records[1:]}  # exclude CLUSTER-001

    with pytest.raises(CatalogueDataValidationError, match="Missing source product in catalogue: 'CLUSTER-001'"):
        validate_dataset_records(records, catalogue_codes=incomplete_catalogue)


def test_missing_recommended_product_detection(db_session: Session) -> None:
    """Test 5: Detect when a mapping references a recommended product not in the catalogue."""
    records = _build_valid_cluster()
    first = records[0]
    bad_mappings = list(first.mappings)
    bad_mappings[5] = SourceMappingEntry(
        source_product_code=first.product_code,
        recommended_product_code="NON-EXISTENT-999",
        recommended_product_name="Ghost Product",
        recommendation_rank=6,
        source_excel_row=2,
    )
    records[0] = SourceProductRecord(
        product_code=first.product_code,
        product_name=first.product_name,
        source_excel_row=first.source_excel_row,
        mappings=tuple(bad_mappings),
    )

    loader = CatalogueLoaderService(db_session)
    with pytest.raises(
        CatalogueDataValidationError,
        match="references missing recommended product 'NON-EXISTENT-999'",
    ):
        loader.load_records(records)


def test_duplicate_mapping_detection() -> None:
    """Test 6: Detect duplicate (source, rank) and duplicate (source, recommended_product) pairs."""
    records = _build_valid_cluster()
    first = records[0]

    # Duplicate target product within same source row
    dup_target_mappings = list(first.mappings)
    dup_target_mappings[5] = SourceMappingEntry(
        source_product_code=first.product_code,
        recommended_product_code=dup_target_mappings[0].recommended_product_code,
        recommended_product_name=dup_target_mappings[0].recommended_product_name,
        recommendation_rank=6,
        source_excel_row=2,
    )
    records_dup_target = [
        SourceProductRecord(
            product_code=first.product_code,
            product_name=first.product_name,
            source_excel_row=2,
            mappings=tuple(dup_target_mappings),
        ),
        *records[1:],
    ]
    with pytest.raises(
        CatalogueDataValidationError,
        match=r"Duplicate \(source_product_code, recommended_product_code\) pair",
    ):
        validate_dataset_records(records_dup_target)

    # Duplicate rank within same source row
    dup_rank_mappings = list(first.mappings)
    dup_rank_mappings[5] = SourceMappingEntry(
        source_product_code=first.product_code,
        recommended_product_code=dup_rank_mappings[5].recommended_product_code,
        recommended_product_name=dup_rank_mappings[5].recommended_product_name,
        recommendation_rank=1,  # duplicate rank 1
        source_excel_row=2,
    )
    records_dup_rank = [
        SourceProductRecord(
            product_code=first.product_code,
            product_name=first.product_name,
            source_excel_row=2,
            mappings=tuple(dup_rank_mappings),
        ),
        *records[1:],
    ]
    with pytest.raises(
        CatalogueDataValidationError,
        match=r"Duplicate \(source_product_code, recommendation_rank\) pair",
    ):
        validate_dataset_records(records_dup_rank)


def test_invalid_rank_detection() -> None:
    """Test 7: Detect recommendation rank outside 1..6."""
    records = _build_valid_cluster()
    first = records[0]
    bad_rank_mappings = list(first.mappings)
    bad_rank_mappings[5] = SourceMappingEntry(
        source_product_code=first.product_code,
        recommended_product_code=bad_rank_mappings[5].recommended_product_code,
        recommended_product_name=bad_rank_mappings[5].recommended_product_name,
        recommendation_rank=7,
        source_excel_row=2,
    )
    records[0] = SourceProductRecord(
        product_code=first.product_code,
        product_name=first.product_name,
        source_excel_row=2,
        mappings=tuple(bad_rank_mappings),
    )

    with pytest.raises(CatalogueDataValidationError, match="invalid rank 7"):
        validate_dataset_records(records)


def test_self_recommendation_detection() -> None:
    """Test 8: Detect self-recommendation where source_product_code == recommended_product_code."""
    records = _build_valid_cluster()
    first = records[0]
    self_mappings = list(first.mappings)
    self_mappings[0] = SourceMappingEntry(
        source_product_code=first.product_code,
        recommended_product_code=first.product_code,
        recommended_product_name=first.product_name,
        recommendation_rank=1,
        source_excel_row=2,
    )
    records[0] = SourceProductRecord(
        product_code=first.product_code,
        product_name=first.product_name,
        source_excel_row=2,
        mappings=tuple(self_mappings),
    )

    with pytest.raises(CatalogueDataValidationError, match="Self-recommendation detected"):
        validate_dataset_records(records)


def test_missing_recommendation_rank_detection() -> None:
    """Test 9: Detect when a source product has fewer than 6 mappings / missing rank."""
    records = _build_valid_cluster()
    first = records[0]
    # Drop rank 6 so only 5 mappings exist
    records[0] = SourceProductRecord(
        product_code=first.product_code,
        product_name=first.product_name,
        source_excel_row=2,
        mappings=first.mappings[:5],
    )

    with pytest.raises(CatalogueDataValidationError, match="must have exactly 6 mappings, found 5"):
        validate_dataset_records(records)
