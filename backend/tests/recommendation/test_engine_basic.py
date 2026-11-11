"""Stage 4 basic allocation and happy-path tests (Scenarios 1..7) plus DB integration."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.recommendation import RecommendationResultSchema
from app.services.catalogue_loader import CatalogueLoaderService
from app.services.recommendation import (
    DatabaseRecommendationService,
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import build_independent_clusters_fixture


@pytest.mark.parametrize(
    ("num_sources", "expected_quotas", "expected_picks_per_source"),
    [
        # Scenario 1: 1 mapped source -> quota [6]
        (1, [6], {"SRC-01": ["SRC-01-T1", "SRC-01-T2", "SRC-01-T3", "SRC-01-T4", "SRC-01-T5", "SRC-01-T6"]}),
        # Scenario 2: 2 mapped sources -> quotas [3, 3]
        (2, [3, 3], {"SRC-01": ["SRC-01-T1", "SRC-01-T2", "SRC-01-T3"], "SRC-02": ["SRC-02-T1", "SRC-02-T2", "SRC-02-T3"]}),
        # Scenario 3: 3 mapped sources -> quotas [2, 2, 2]
        (3, [2, 2, 2], {"SRC-01": ["SRC-01-T1", "SRC-01-T2"], "SRC-02": ["SRC-02-T1", "SRC-02-T2"], "SRC-03": ["SRC-03-T1", "SRC-03-T2"]}),
        # Scenario 4: 4 mapped sources -> quotas [2, 2, 1, 1]
        (4, [2, 2, 1, 1], {"SRC-01": ["SRC-01-T1", "SRC-01-T2"], "SRC-02": ["SRC-02-T1", "SRC-02-T2"], "SRC-03": ["SRC-03-T1"], "SRC-04": ["SRC-04-T1"]}),
        # Scenario 5: 5 mapped sources -> quotas [2, 1, 1, 1, 1]
        (5, [2, 1, 1, 1, 1], {"SRC-01": ["SRC-01-T1", "SRC-01-T2"], "SRC-02": ["SRC-02-T1"], "SRC-03": ["SRC-03-T1"], "SRC-04": ["SRC-04-T1"], "SRC-05": ["SRC-05-T1"]}),
        # Scenario 6: 6 mapped sources -> quotas [1, 1, 1, 1, 1, 1]
        (6, [1, 1, 1, 1, 1, 1], {f"SRC-{i:02d}": [f"SRC-{i:02d}-T1"] for i in range(1, 7)}),
        # Scenario 7: 7 mapped sources (>6) -> quotas [1, 1, 1, 1, 1, 1, 0]
        (
            7,
            [1, 1, 1, 1, 1, 1, 0],
            {
                **{f"SRC-{i:02d}": [f"SRC-{i:02d}-T1"] for i in range(1, 7)},
                "SRC-07": [],
            },
        ),
    ],
)
def test_basic_slot_allocation_scenarios_1_to_7(
    num_sources: int,
    expected_quotas: list[int],
    expected_picks_per_source: dict[str, list[str]],
) -> None:
    catalogue, mappings = build_independent_clusters_fixture(num_sources=8)
    order = OrderInput(
        order_id=f"ORD-BASIC-{num_sources}",
        line_items=[
            OrderLineInput(product_code=f"SRC-{i:02d}", quantity=1, line_index=i - 1)
            for i in range(1, num_sources + 1)
        ],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert result.unfilled_slots == 0
    assert all(
        r.selection_phase == SelectionPhase.INITIAL_ALLOCATION
        for r in result.recommendations
    )

    actual_quotas = [s.initial_quota for s in result.trace.source_evaluations]
    assert actual_quotas == expected_quotas

    for src_eval in result.trace.source_evaluations:
        assert src_eval.selected_skus == expected_picks_per_source[src_eval.product_code]

    # Validate result against Pydantic schema
    validated = RecommendationResultSchema.model_validate(result.to_dict())
    assert validated.trace.selected_recommendation_count == 6


def test_database_recommendation_service_batch_integration(db_session: Session) -> None:
    """Verify `DatabaseRecommendationService` batch-loads mappings and catalogue metadata from PostgreSQL."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )

    service = DatabaseRecommendationService(db_session)
    # Two sources from different isolated mapping clusters (Cluster 1: SAMPLE-001, Cluster 2: SAMPLE-008)
    order = OrderInput(
        order_id="ORD-DB-INT-01",
        line_items=[
            OrderLineInput(product_code="SAMPLE-001", quantity=1, line_index=0),
            OrderLineInput(product_code="SAMPLE-008", quantity=1, line_index=1),
        ],
    )
    result = service.generate_for_order_input(order)

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert result.trace.ordered_recommended_skus == [
        "SAMPLE-002",
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-010",
        "SAMPLE-011",
        "SAMPLE-012",
    ]
