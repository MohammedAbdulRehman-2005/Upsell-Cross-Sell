"""Stage 3 tests for idempotent seeding, dry-run rollback, and transaction rollback on failure."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import CatalogueMetadataSource
from app.repositories.product_repository import ProductRepository
from app.schemas.product import ProductCreate, ProductMappingCreate
from app.services.catalogue_loader import (
    CatalogueDataValidationError,
    CatalogueLoaderService,
)


def test_seed_idempotency_across_repeated_executions(db_session: Session) -> None:
    """Test 10: Running the seed process multiple times does not create duplicates
    and preserves unrelated catalogue products.
    """
    settings = get_settings()
    repo = ProductRepository(db_session)

    # Pre-insert an unrelated broader-catalogue product (outside the 70 mapped SKUs)
    repo.create(
        ProductCreate(
            product_code="BROADER-CAT-1001",
            product_name="Unmapped Vitamin C Serum",
            is_rx=False,
            is_active=True,
            available_quantity=50,
            metadata_source=CatalogueMetadataSource.VERIFIED_CATALOGUE,
            is_synthetic_metadata=False,
        )
    )

    loader = CatalogueLoaderService(db_session)

    # First run: inserts 70 workbook products and 420 mappings
    first_run = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        apply_synthetic_metadata=False,
    )
    assert first_run.products_inserted == 70
    assert first_run.products_updated == 0
    assert first_run.products_unchanged == 0
    assert first_run.mappings_inserted == 420
    assert first_run.mappings_updated == 0
    assert first_run.mappings_unchanged == 0

    # Second run: 0 inserts, 0 updates, 70 unchanged products, 420 unchanged mappings
    second_run = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        apply_synthetic_metadata=False,
    )
    assert second_run.products_inserted == 0
    assert second_run.products_updated == 0
    assert second_run.products_unchanged == 70
    assert second_run.mappings_inserted == 0
    assert second_run.mappings_updated == 0
    assert second_run.mappings_unchanged == 420

    # Unrelated broader catalogue product is still preserved intact (70 + 1 = 71 total products)
    all_products = repo.list_all(limit=200)
    assert len(all_products) == 71
    assert repo.get_by_code("BROADER-CAT-1001") is not None
    assert len(repo.list_all_mappings()) == 420


def test_transaction_rollback_on_critical_conflict(db_session: Session) -> None:
    """Test 11: If a critical conflict occurs mid-load (e.g. an existing mapping conflicts
    with the authoritative workbook), the entire transaction rolls back cleanly.
    """
    settings = get_settings()
    repo = ProductRepository(db_session)

    # Pre-seed two products and a conflicting mapping for SAMPLE-001 rank 1 -> SAMPLE-007
    # (Whereas the authoritative workbook maps SAMPLE-001 rank 1 -> SAMPLE-002)
    repo.create(
        ProductCreate(
            product_code="SAMPLE-001",
            product_name="Sample Gentle Face Cleanser",
        )
    )
    repo.create(
        ProductCreate(
            product_code="SAMPLE-007",
            product_name="Sample Nourishing Face Oil",
        )
    )
    repo.create_mapping(
        ProductMappingCreate(
            source_product_code="SAMPLE-001",
            recommended_product_code="SAMPLE-007",
            recommendation_rank=1,
            source_excel_row=2,
        )
    )

    loader = CatalogueLoaderService(db_session)
    with pytest.raises(CatalogueDataValidationError, match="Mapping conflict for source 'SAMPLE-001'"):
        loader.load_from_files(
            workbook_path=settings.raw_workbook_path,
            normalized_json_path=settings.normalized_mappings_path,
            normalized_csv_path=settings.normalized_mappings_csv_path,
            validation_report_path=settings.validation_report_path,
        )

    # Transaction must have rolled back: the 68 new products inserted before Step 2 must not persist
    remaining_products = repo.list_all(limit=200)
    assert [p.product_code for p in remaining_products] == ["SAMPLE-001", "SAMPLE-007"]
    remaining_mappings = repo.list_all_mappings()
    assert len(remaining_mappings) == 1


def test_dry_run_mode_does_not_persist_records(db_session: Session) -> None:
    """Verify dry_run=True validates and simulates insertion, then rolls back cleanly."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)

    summary = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.products_inserted == 70
    assert summary.mappings_inserted == 420

    repo = ProductRepository(db_session)
    assert len(repo.list_all(limit=200)) == 0
    assert len(repo.list_all_mappings()) == 0
