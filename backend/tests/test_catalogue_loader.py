"""Stage 3 tests for product catalogue loading, rank preservation, and metadata provenance."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import CatalogueMetadataSource
from app.repositories.product_repository import ProductRepository
from app.schemas.product import ProductCreate
from app.services.catalogue_loader import (
    CatalogueLoaderService,
    load_synthetic_metadata_fixture,
    verify_database_catalogue,
    verify_source_files_consistency,
)


def test_successful_product_and_mapping_loading_preserves_unknown_metadata(
    db_session: Session,
) -> None:
    """Tests 1, 2, 3, and 14:
    - Loads all 70 workbook products and 420 ranked mappings.
    - Preserves original recommendation ranks 1..6.
    - Preserves unknown eligibility metadata (`is_rx=None`, `is_active=None`, `available_quantity=None`)
      when no verified or synthetic metadata is provided.
    """
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)

    summary = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        apply_synthetic_metadata=False,
    )

    assert summary.source_products_count == 70
    assert summary.source_mappings_count == 420
    assert summary.products_inserted == 70
    assert summary.products_updated == 0
    assert summary.products_unchanged == 0
    assert summary.mappings_inserted == 420
    assert summary.mappings_updated == 0
    assert summary.mappings_unchanged == 0

    repo = ProductRepository(db_session)
    all_products = repo.list_all(limit=100)
    assert len(all_products) == 70

    # Verify unknown metadata state is strictly preserved (not silently defaulted to eligible)
    sample_001 = repo.get_by_code("SAMPLE-001")
    assert sample_001 is not None
    assert sample_001.product_name == "Sample Gentle Face Cleanser"
    assert sample_001.is_rx is None
    assert sample_001.is_active is None
    assert sample_001.available_quantity is None
    assert sample_001.unit_price is None
    assert sample_001.metadata_source == CatalogueMetadataSource.WORKBOOK_ONLY
    assert sample_001.is_synthetic_metadata is False

    # Verify exact rank preservation for SAMPLE-001 (1..6)
    mappings_001 = repo.list_mappings_for_source("SAMPLE-001")
    assert [(m.recommendation_rank, m.recommended_product_code) for m in mappings_001] == [
        (1, "SAMPLE-002"),
        (2, "SAMPLE-003"),
        (3, "SAMPLE-004"),
        (4, "SAMPLE-005"),
        (5, "SAMPLE-006"),
        (6, "SAMPLE-007"),
    ]

    verification = verify_database_catalogue(db_session)
    assert verification.status == "PASS"
    assert verification.workbook_products_loaded == 70
    assert verification.mapping_records_loaded == 420
    assert verification.workbook_only_products == 70
    assert verification.unknown_eligibility_products == 70
    assert verification.synthetic_metadata_products == 0


def test_synthetic_metadata_identification_and_verified_metadata_preservation(
    db_session: Session,
) -> None:
    """Tests 12 and 13:
    - Synthetic fixture records are explicitly tagged (`metadata_source=SYNTHETIC_FIXTURE`,
      `is_synthetic_metadata=True`).
    - Existing `VERIFIED_CATALOGUE` metadata is never overwritten by subsequent synthetic
      or workbook-only seed executions.
    """
    settings = get_settings()
    repo = ProductRepository(db_session)

    # Pre-insert a verified catalogue product for SAMPLE-001 with authoritative stock/price/Rx
    repo.create(
        ProductCreate(
            product_code="SAMPLE-001",
            product_name="Sample Gentle Face Cleanser",
            is_rx=True,
            is_active=False,
            available_quantity=999,
            unit_price=Decimal("1250.00"),
            currency="INR",
            metadata_source=CatalogueMetadataSource.VERIFIED_CATALOGUE,
            is_synthetic_metadata=False,
        )
    )

    loader = CatalogueLoaderService(db_session)
    summary = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )

    assert summary.products_inserted == 69
    assert summary.products_unchanged == 1
    assert summary.verified_metadata_preserved == 1
    assert summary.mappings_inserted == 420

    # Verified product SAMPLE-001 must retain its verified attributes untouched
    p_verified = repo.get_by_code("SAMPLE-001")
    assert p_verified is not None
    assert p_verified.metadata_source == CatalogueMetadataSource.VERIFIED_CATALOGUE
    assert p_verified.is_synthetic_metadata is False
    assert p_verified.is_rx is True
    assert p_verified.is_active is False
    assert p_verified.available_quantity == 999
    assert p_verified.unit_price == Decimal("1250.00")

    # Synthetic fixture product SAMPLE-002 must be explicitly identifiable as synthetic
    p_synthetic = repo.get_by_code("SAMPLE-002")
    assert p_synthetic is not None
    assert p_synthetic.metadata_source == CatalogueMetadataSource.SYNTHETIC_FIXTURE
    assert p_synthetic.is_synthetic_metadata is True
    assert p_synthetic.is_rx is False
    assert p_synthetic.is_active is True
    assert p_synthetic.available_quantity == 25

    verification = verify_database_catalogue(db_session)
    assert verification.status == "PASS"
    assert verification.verified_metadata_products == 1
    assert verification.synthetic_metadata_products == 69


def test_source_files_cross_verification_passes() -> None:
    """Verify workbook, normalized JSON, normalized CSV, and validation report agree."""
    settings = get_settings()
    records = verify_source_files_consistency(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
    )
    assert len(records) == 70
    assert sum(len(r.mappings) for r in records) == 420

    synth = load_synthetic_metadata_fixture(settings.synthetic_catalogue_fixture_path)
    assert len(synth) == 70
