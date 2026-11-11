"""Stage 4.2 PostgreSQL metadata-provenance verification tests (Scenario A, Scenario B, and Metadata Safety)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import CatalogueMetadataSource
from app.repositories.product_repository import ProductRepository
from app.schemas.product import ProductUpdate
from app.services.catalogue_loader import CatalogueLoaderService
from app.services.recommendation import (
    CandidateExclusionReason,
    DatabaseRecommendationService,
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
)
from tests.recommendation.test_stage4_1_audit import assert_trace_counters_reconcile


def test_scenario_a_workbook_only_postgresql_verification(db_session: Session) -> None:
    """Scenario A: Workbook-only PostgreSQL database has 70 WORKBOOK_ONLY rows with NULL eligibility and fails closed."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    summary = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )
    assert summary.synthetic_fixture_applied is False
    assert summary.products_inserted == 70
    assert summary.mappings_inserted == 420

    # Verify via direct SQL query against PostgreSQL
    rows = db_session.execute(
        text(
            """
            SELECT
                metadata_source,
                is_synthetic_metadata,
                COUNT(*) AS total_products,
                COUNT(is_rx) AS non_null_is_rx,
                COUNT(is_active) AS non_null_is_active,
                COUNT(available_quantity) AS non_null_available_quantity,
                COUNT(unit_price) AS non_null_unit_price
            FROM products
            GROUP BY metadata_source, is_synthetic_metadata
            ORDER BY metadata_source
            """
        )
    ).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["metadata_source"] == "WORKBOOK_ONLY"
    assert row["is_synthetic_metadata"] is False
    assert row["total_products"] == 70
    assert row["non_null_is_rx"] == 0
    assert row["non_null_is_active"] == 0
    assert row["non_null_available_quantity"] == 0
    assert row["non_null_unit_price"] == 0

    # Run recommendation engine via DatabaseRecommendationService
    service = DatabaseRecommendationService(db_session)
    order = OrderInput(
        order_id="ORD-STAGE42-SCENARIO-A",
        line_items=[
            OrderLineInput("SAMPLE-001", quantity=1, line_index=0),
            OrderLineInput("SAMPLE-008", quantity=10, line_index=1),
        ],
    )
    result = service.generate_for_order_input(order)
    assert_trace_counters_reconcile(result)

    assert result.status == RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS
    assert result.recommendations == []
    assert result.unfilled_slots == 6
    assert result.trace.order_summary.catalogue_metadata_sources_used == ["WORKBOOK_ONLY"]
    assert result.trace.order_summary.contains_synthetic_metadata is False

    for src_trace in result.trace.source_evaluations:
        assert src_trace.metadata_source == "WORKBOOK_ONLY"
        assert src_trace.is_synthetic_metadata is False
        assert src_trace.mapping_count == 6
        assert src_trace.eligibility_evaluations_count == 6
        assert src_trace.eligible_candidate_count == 0
        assert src_trace.rejected_candidate_count == 6
        assert src_trace.allocation_candidates_examined == 6
        assert src_trace.unvisited_candidate_count == 0

    assert len(result.trace.candidate_evaluations) == 12
    for cand_trace in result.trace.candidate_evaluations:
        assert cand_trace.metadata_source == "WORKBOOK_ONLY"
        assert cand_trace.is_synthetic_metadata is False
        assert cand_trace.is_eligible is False
        assert cand_trace.eligibility_rejection_reasons == [
            CandidateExclusionReason.CATALOGUE_METADATA_MISSING
        ]
        assert cand_trace.selected is False


def test_scenario_b_synthetic_demonstration_metadata_postgresql_verification(
    db_session: Session,
) -> None:
    """Scenario B: Applying synthetic fixture marks all 70 products SYNTHETIC_FIXTURE (none VERIFIED_CATALOGUE) and records provenance in trace."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    summary = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )
    assert summary.synthetic_fixture_applied is True
    assert summary.products_inserted == 70
    assert summary.mappings_inserted == 420

    # Verify via direct SQL query against PostgreSQL
    rows = db_session.execute(
        text(
            """
            SELECT
                metadata_source,
                is_synthetic_metadata,
                COUNT(*) AS total_products,
                COUNT(*) FILTER (WHERE is_rx IS TRUE) AS rx_true_count,
                COUNT(*) FILTER (WHERE is_active IS FALSE) AS inactive_count,
                COUNT(*) FILTER (WHERE available_quantity = 0) AS zero_stock_count,
                COUNT(*) FILTER (WHERE is_rx IS NULL OR is_active IS NULL OR available_quantity IS NULL) AS unknown_meta_count
            FROM products
            GROUP BY metadata_source, is_synthetic_metadata
            ORDER BY metadata_source
            """
        )
    ).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["metadata_source"] == "SYNTHETIC_FIXTURE"
    assert row["is_synthetic_metadata"] is True
    assert row["total_products"] == 70

    # Confirm 0 products are represented as VERIFIED_CATALOGUE
    verified_count = db_session.execute(
        text("SELECT COUNT(*) FROM products WHERE metadata_source = 'VERIFIED_CATALOGUE'")
    ).scalar_one()
    assert verified_count == 0

    service = DatabaseRecommendationService(db_session)
    order = OrderInput(
        order_id="ORD-STAGE42-SCENARIO-B",
        line_items=[
            OrderLineInput("SAMPLE-001", quantity=1, line_index=0),
            OrderLineInput("SAMPLE-008", quantity=10, line_index=1),
        ],
    )
    result = service.generate_for_order_input(order)
    assert_trace_counters_reconcile(result)

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert result.unfilled_slots == 0
    assert result.trace.order_summary.catalogue_metadata_sources_used == ["SYNTHETIC_FIXTURE"]
    assert result.trace.order_summary.contains_synthetic_metadata is True

    assert [r.recommended_product_code for r in result.recommendations] == [
        "SAMPLE-002",
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-010",
        "SAMPLE-011",
        "SAMPLE-012",
    ]
    for rec in result.recommendations:
        assert rec.metadata_source == "SYNTHETIC_FIXTURE"
        assert rec.is_synthetic_metadata is True

    for src_trace in result.trace.source_evaluations:
        assert src_trace.metadata_source == "SYNTHETIC_FIXTURE"
        assert src_trace.is_synthetic_metadata is True

    for cand_trace in result.trace.candidate_evaluations:
        assert cand_trace.metadata_source == "SYNTHETIC_FIXTURE"
        assert cand_trace.is_synthetic_metadata is True


def test_metadata_safety_verified_preservation_and_idempotency_in_postgresql(
    db_session: Session,
) -> None:
    """Verify VERIFIED_CATALOGUE is never overwritten by synthetic or workbook-only seeding, and repeated runs are idempotent."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    repo = ProductRepository(db_session)

    # 1. Initial workbook-only seed
    s1 = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )
    assert s1.products_inserted == 70

    # 2. Promote SAMPLE-001 and SAMPLE-002 to VERIFIED_CATALOGUE
    for code in ("SAMPLE-001", "SAMPLE-002"):
        prod = repo.get_by_code(code)
        assert prod is not None
        repo.update(
            prod,
            ProductUpdate(
                is_rx=False,
                is_active=True,
                available_quantity=999,
                unit_price=Decimal("777.00"),
                currency="INR",
                metadata_source=CatalogueMetadataSource.VERIFIED_CATALOGUE,
                is_synthetic_metadata=False,
            ),
            commit=True,
        )

    # 3. Run synthetic fixture seeding -> must preserve the 2 VERIFIED_CATALOGUE rows and update the 68 WORKBOOK_ONLY rows
    s2 = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )
    assert s2.verified_metadata_preserved == 2
    assert s2.products_updated == 68
    assert s2.products_unchanged == 2
    assert s2.mappings_unchanged == 420

    # 4. Repeat synthetic fixture seeding -> 100% idempotent (0 inserts, 0 updates, 70 unchanged)
    s3 = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )
    assert s3.products_inserted == 0
    assert s3.products_updated == 0
    assert s3.products_unchanged == 70
    assert s3.verified_metadata_preserved == 2
    assert s3.mappings_inserted == 0
    assert s3.mappings_updated == 0
    assert s3.mappings_unchanged == 420

    # 5. Re-run workbook-only seeding (apply_synthetic_metadata=False) -> must NOT erase VERIFIED_CATALOGUE metadata
    s4 = loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )
    assert s4.verified_metadata_preserved == 2
    assert s4.products_updated == 0
    assert s4.products_unchanged == 70

    db_session.expire_all()
    for code in ("SAMPLE-001", "SAMPLE-002"):
        verified_prod = repo.get_by_code(code)
        assert verified_prod is not None
        assert verified_prod.metadata_source == CatalogueMetadataSource.VERIFIED_CATALOGUE
        assert verified_prod.is_synthetic_metadata is False
        assert verified_prod.available_quantity == 999
        assert verified_prod.unit_price == Decimal("777.00")

    # 6. Verify SQL consistency between metadata_source and is_synthetic_metadata across all 70 rows
    inconsistent_count = db_session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM products
            WHERE (metadata_source = 'SYNTHETIC_FIXTURE' AND is_synthetic_metadata IS FALSE)
               OR (metadata_source IN ('WORKBOOK_ONLY', 'VERIFIED_CATALOGUE') AND is_synthetic_metadata IS TRUE)
            """
        )
    ).scalar_one()
    assert inconsistent_count == 0

    # 7. Verify mixed provenance is accurately reported in DecisionTrace
    service = DatabaseRecommendationService(db_session)
    mixed_res = service.generate_for_order_input(
        OrderInput("ORD-STAGE42-MIXED-PROVENANCE", [OrderLineInput("SAMPLE-001", 1, 0)])
    )
    assert_trace_counters_reconcile(mixed_res)
    assert mixed_res.trace.order_summary.catalogue_metadata_sources_used == [
        "SYNTHETIC_FIXTURE",
        "VERIFIED_CATALOGUE",
    ]
    assert mixed_res.trace.order_summary.contains_synthetic_metadata is True
    assert mixed_res.recommendations[0].recommended_product_code == "SAMPLE-002"
    assert mixed_res.recommendations[0].metadata_source == "VERIFIED_CATALOGUE"
    assert mixed_res.recommendations[0].is_synthetic_metadata is False
    assert mixed_res.recommendations[0].unit_price == "777.00"
    for rec in mixed_res.recommendations[1:]:
        assert rec.metadata_source == "SYNTHETIC_FIXTURE"
        assert rec.is_synthetic_metadata is True
