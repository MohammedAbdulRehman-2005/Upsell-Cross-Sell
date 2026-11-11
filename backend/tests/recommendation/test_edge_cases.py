"""Tests for unmapped sources, zero eligible candidates, and candidate exhaustion edge cases (Scenarios 19, 20, 21, 22, 33)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.catalogue_loader import CatalogueLoaderService
from app.services.recommendation import (
    CandidateExclusionReason,
    DatabaseRecommendationService,
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import (
    build_independent_clusters_fixture,
    make_catalogue_item,
    make_six_mappings,
)


def test_scenario_19_source_without_mappings_alongside_mapped_source() -> None:
    """Scenario 19: Unmapped source records NO_MAPPING_FOUND; remaining mapped source gets all 6 slots."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=1)
    catalogue["UNMAPPED-SKU-999"] = make_catalogue_item("UNMAPPED-SKU-999")

    order = OrderInput(
        order_id="ORD-S19",
        line_items=[
            OrderLineInput("UNMAPPED-SKU-999", quantity=5, line_index=0),
            OrderLineInput("SRC-01", quantity=1, line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert result.unfilled_slots == 0
    assert result.trace.order_summary.distinct_source_sku_count == 2
    assert result.trace.order_summary.mapped_source_sku_count == 1
    assert result.trace.order_summary.unmapped_source_sku_count == 1

    unmapped_trace = next(
        s for s in result.trace.source_evaluations if s.product_code == "UNMAPPED-SKU-999"
    )
    assert unmapped_trace.mapping_found is False
    assert unmapped_trace.status_reason == CandidateExclusionReason.NO_MAPPING_FOUND.value
    assert unmapped_trace.source_priority_position is None
    assert unmapped_trace.initial_quota == 0

    mapped_trace = next(
        s for s in result.trace.source_evaluations if s.product_code == "SRC-01"
    )
    assert mapped_trace.mapping_found is True
    assert mapped_trace.source_priority_position == 1
    assert mapped_trace.initial_quota == 6


def test_scenario_20_all_sources_without_mappings() -> None:
    """Scenario 20: Order with only unmapped SKUs returns NO_MAPPED_SOURCE_PRODUCTS and 0 recommendations."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=1)
    order = OrderInput(
        order_id="ORD-S20",
        line_items=[
            OrderLineInput("UNKNOWN-001", quantity=1, line_index=0),
            OrderLineInput("UNKNOWN-002", quantity=2, line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.NO_MAPPED_SOURCE_PRODUCTS
    assert result.recommendations == []
    assert result.unfilled_slots == 6
    assert result.trace.order_summary.mapped_source_sku_count == 0
    assert result.trace.order_summary.unmapped_source_sku_count == 2
    assert all(
        s.status_reason == CandidateExclusionReason.NO_MAPPING_FOUND.value
        for s in result.trace.source_evaluations
    )


def test_scenario_21_one_source_all_candidates_ineligible_other_source_backfills() -> None:
    """Scenario 21: One mapped source has 0 eligible candidates; other mapped source fills all 6 slots via initial + backfill."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    # Make all 6 candidates of SRC-01 Rx
    for rank in range(1, 7):
        code = f"SRC-01-T{rank}"
        catalogue[code] = make_catalogue_item(code, is_rx=True)

    order = OrderInput(
        order_id="ORD-S21",
        line_items=[
            OrderLineInput("SRC-01", line_index=0),
            OrderLineInput("SRC-02", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    # SRC-02 has 6 eligible -> priority pos 1, initial quota 3 -> T1..T3 (INITIAL_ALLOCATION)
    # SRC-01 has 0 eligible -> priority pos 2, initial quota 3 -> 0 selected (shortfall 3)
    # Backfill from SRC-02 -> T4..T6 (BACKFILL)
    assert [r.recommended_product_code for r in result.recommendations] == [
        f"SRC-02-T{r}" for r in range(1, 7)
    ]
    assert [r.selection_phase for r in result.recommendations] == [
        SelectionPhase.INITIAL_ALLOCATION,
        SelectionPhase.INITIAL_ALLOCATION,
        SelectionPhase.INITIAL_ALLOCATION,
        SelectionPhase.BACKFILL,
        SelectionPhase.BACKFILL,
        SelectionPhase.BACKFILL,
    ]


def test_scenario_22_all_mapped_sources_have_zero_eligible_candidates() -> None:
    """Scenario 22: Mapped sources exist but all candidates are ineligible -> NO_ELIGIBLE_RECOMMENDATIONS."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    for rank in range(1, 7):
        catalogue[f"SRC-01-T{rank}"] = make_catalogue_item(f"SRC-01-T{rank}", is_rx=True)
        catalogue[f"SRC-02-T{rank}"] = make_catalogue_item(
            f"SRC-02-T{rank}", available_quantity=0
        )

    order = OrderInput(
        order_id="ORD-S22",
        line_items=[
            OrderLineInput("SRC-01", line_index=0),
            OrderLineInput("SRC-02", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS
    assert result.recommendations == []
    assert result.unfilled_slots == 6
    assert result.trace.order_summary.mapped_source_sku_count == 2


def test_scenario_33_candidates_exhausted_before_six_slots_filled_returns_partial_success() -> None:
    """Scenario 33: Two sources in the same 7-SKU isolated mapping cluster yield 5 unique items (PARTIAL_SUCCESS)."""
    # Cluster 1: SAMPLE-001 .. SAMPLE-007 (closed clique of 7 products)
    cluster_codes = [f"SAMPLE-{i:03d}" for i in range(1, 8)]
    catalogue = {code: make_catalogue_item(code) for code in cluster_codes}
    mappings = []
    for src in cluster_codes:
        targets = [c for c in cluster_codes if c != src]
        mappings.extend(make_six_mappings(src, targets))

    order = OrderInput(
        order_id="ORD-S33-CLUSTER-CLIQUE",
        line_items=[
            OrderLineInput("SAMPLE-001", line_index=0),
            OrderLineInput("SAMPLE-002", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.PARTIAL_SUCCESS
    assert len(result.recommendations) == 5
    assert result.unfilled_slots == 1
    assert [r.recommended_product_code for r in result.recommendations] == [
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-005",
        "SAMPLE-006",
        "SAMPLE-007",
    ]


def test_workbook_only_seeded_catalogue_fails_closed_in_database_service(
    db_session: Session,
) -> None:
    """Verify workbook-only seeded rows (is_rx=None, is_active=None, available_quantity=None) fail closed."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )

    db_rec_service = DatabaseRecommendationService(db_session)
    order = OrderInput(
        order_id="ORD-WORKBOOK-ONLY-FAIL-CLOSED",
        line_items=[OrderLineInput("SAMPLE-001", quantity=1, line_index=0)],
    )
    result = db_rec_service.generate_for_order_input(order)

    assert result.status == RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS
    assert result.recommendations == []
    assert result.unfilled_slots == 6
    assert len(result.trace.candidate_evaluations) == 6
    for cand in result.trace.candidate_evaluations:
        assert cand.is_eligible is False
        assert CandidateExclusionReason.CATALOGUE_METADATA_MISSING in cand.rejection_reasons
