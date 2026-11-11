"""Stage 4.1 targeted audit tests: trace counter reconciliation, M=2 quantity-invariance worked example, and full PostgreSQL integration."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import OrderStatus
from app.repositories.customer_repository import CustomerRepository
from app.repositories.order_repository import OrderRepository
from app.schemas.customer import CustomerCreate
from app.schemas.order import OrderCreate, OrderLineItemCreate
from app.schemas.recommendation import RecommendationResultSchema
from app.services.catalogue_loader import CatalogueLoaderService
from app.services.recommendation import (
    CandidateAllocationDecision,
    CandidateExclusionReason,
    DatabaseRecommendationService,
    OrderInput,
    OrderLineInput,
    RecommendationResult,
    RecommendationResultStatus,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import (
    build_independent_clusters_fixture,
    make_catalogue_item,
    make_six_mappings,
)


def assert_trace_counters_reconcile(result: RecommendationResult) -> None:
    """Assert that every SourceTraceEntry reconciles with its underlying CandidateTraceEntry records without double-counting."""
    # Validate full result against strict Pydantic v2 schema
    RecommendationResultSchema.model_validate(result.to_dict())

    candidates_by_source: dict[str, list] = {}
    for cand in result.trace.candidate_evaluations:
        candidates_by_source.setdefault(cand.source_product_code, []).append(cand)

        # Distinguish pre-prioritization eligibility rejections from allocation decisions
        assert CandidateExclusionReason.DUPLICATE_RECOMMENDATION not in cand.eligibility_rejection_reasons
        if cand.is_eligible:
            assert cand.eligibility_rejection_reasons == []
            if not cand.examined_during_allocation:
                assert cand.allocation_decision == CandidateAllocationDecision.NOT_REACHED
                assert cand.selected is False
                assert cand.excluded_as_duplicate is False
            elif cand.excluded_as_duplicate:
                assert cand.allocation_decision == CandidateAllocationDecision.SKIPPED_DUPLICATE
                assert cand.selected is False
                assert CandidateExclusionReason.DUPLICATE_RECOMMENDATION in cand.rejection_reasons
            else:
                assert cand.selected is True
                if cand.selection_phase == SelectionPhase.INITIAL_ALLOCATION:
                    assert (
                        cand.allocation_decision
                        == CandidateAllocationDecision.SELECTED_INITIAL_ALLOCATION
                    )
                else:
                    assert (
                        cand.allocation_decision
                        == CandidateAllocationDecision.SELECTED_BACKFILL
                    )
        else:
            assert len(cand.eligibility_rejection_reasons) >= 1
            assert cand.selected is False
            assert cand.excluded_as_duplicate is False
            if cand.examined_during_allocation:
                assert cand.allocation_decision == CandidateAllocationDecision.SKIPPED_INELIGIBLE
            else:
                assert cand.allocation_decision == CandidateAllocationDecision.NOT_REACHED

    for src in result.trace.source_evaluations:
        src_cands = candidates_by_source.get(src.product_code, [])
        assert src.mapping_count == len(src_cands)
        assert src.eligibility_evaluations_count == src.mapping_count

        # 1. Eligibility partition: mapping_count == eligible + rejected
        assert (
            src.eligible_candidate_count + src.rejected_candidate_count
            == src.eligibility_evaluations_count
        )
        assert src.eligible_candidate_count == sum(1 for c in src_cands if c.is_eligible)
        assert src.rejected_candidate_count == sum(1 for c in src_cands if not c.is_eligible)

        # 2. Allocation visitation partition: mapping_count == examined + unvisited
        assert (
            src.allocation_candidates_examined + src.unvisited_candidate_count
            == src.mapping_count
        )
        assert src.allocation_candidates_examined == sum(
            1 for c in src_cands if c.examined_during_allocation
        )
        assert src.unvisited_candidate_count == sum(
            1 for c in src_cands if not c.examined_during_allocation
        )

        # 3. Selection phase partition: candidates_selected == initial + backfill
        assert (
            src.initial_allocation_selected_count + src.backfill_selected_count
            == src.candidates_selected
        )
        assert src.initial_allocation_selected_count == sum(
            1
            for c in src_cands
            if c.selected and c.selection_phase == SelectionPhase.INITIAL_ALLOCATION
        )
        assert src.backfill_selected_count == sum(
            1
            for c in src_cands
            if c.selected and c.selection_phase == SelectionPhase.BACKFILL
        )

        # 4. Examined during allocation partition: examined == selected + duplicate + examined_ineligible
        examined_ineligible = sum(
            1 for c in src_cands if c.examined_during_allocation and not c.is_eligible
        )
        assert (
            src.allocation_candidates_examined
            == src.candidates_selected + src.duplicate_candidate_count + examined_ineligible
        )

        # 5. Eligible candidates fate partition: eligible == selected + duplicate + unvisited_eligible
        unvisited_eligible = sum(
            1 for c in src_cands if c.is_eligible and not c.examined_during_allocation
        )
        assert (
            src.eligible_candidate_count
            == src.candidates_selected + src.duplicate_candidate_count + unvisited_eligible
        )

        # 6. SKU lists match counters
        assert len(src.selected_skus) == src.candidates_selected
        assert len(src.rejected_skus) == src.rejected_candidate_count
        assert len(src.duplicate_skus) == src.duplicate_candidate_count
        assert len(src.unvisited_skus) == src.unvisited_candidate_count


def test_stage4_1_src02_backfill_trace_accounting_reconciles_unvisited_candidate() -> None:
    """Verify the exact SRC-02 / SRC-01 backfill trace where 6 candidates are eligibility-checked and 5 are examined during allocation."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    catalogue["SRC-01-T1"] = make_catalogue_item("SRC-01-T1", is_rx=True)
    catalogue["SRC-01-T2"] = make_catalogue_item("SRC-01-T2", is_active=False)
    catalogue["SRC-01-T3"] = make_catalogue_item("SRC-01-T3", available_quantity=0)
    catalogue["SRC-01-T4"] = make_catalogue_item("SRC-01-T4", available_quantity=0)
    catalogue["SRC-01-T5"] = make_catalogue_item("SRC-01-T5", is_rx=True)

    order = OrderInput(
        order_id="EX-4-EXCLUSIONS-BACKFILL",
        line_items=[
            OrderLineInput("SRC-01", quantity=1, line_index=0),
            OrderLineInput("SRC-02", quantity=1, line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert_trace_counters_reconcile(result)

    src02_trace = next(
        s for s in result.trace.source_evaluations if s.product_code == "SRC-02"
    )
    assert src02_trace.mapping_count == 6
    assert src02_trace.eligibility_evaluations_count == 6
    assert src02_trace.eligible_candidate_count == 6
    assert src02_trace.rejected_candidate_count == 0
    assert src02_trace.allocation_candidates_examined == 5
    assert src02_trace.initial_allocation_selected_count == 3
    assert src02_trace.backfill_selected_count == 2
    assert src02_trace.candidates_selected == 5
    assert src02_trace.duplicate_candidate_count == 0
    assert src02_trace.unvisited_candidate_count == 1
    assert src02_trace.unvisited_skus == ["SRC-02-T6"]

    # Check the 6th candidate of SRC-02 explicitly
    t6_cand = next(
        c
        for c in result.trace.candidate_evaluations
        if c.source_product_code == "SRC-02" and c.candidate_product_code == "SRC-02-T6"
    )
    assert t6_cand.is_eligible is True
    assert t6_cand.eligibility_rejection_reasons == []
    assert t6_cand.examined_during_allocation is False
    assert t6_cand.allocation_decision == CandidateAllocationDecision.NOT_REACHED
    assert t6_cand.selected is False

    src01_trace = next(
        s for s in result.trace.source_evaluations if s.product_code == "SRC-01"
    )
    assert src01_trace.mapping_count == 6
    assert src01_trace.eligibility_evaluations_count == 6
    assert src01_trace.eligible_candidate_count == 1
    assert src01_trace.rejected_candidate_count == 5
    assert src01_trace.allocation_candidates_examined == 6
    assert src01_trace.initial_allocation_selected_count == 1
    assert src01_trace.backfill_selected_count == 0
    assert src01_trace.candidates_selected == 1
    assert src01_trace.duplicate_candidate_count == 0
    assert src01_trace.unvisited_candidate_count == 0


def test_stage4_1_m_equals_2_worked_example_with_different_quantities() -> None:
    """Verify M=2 worked example: 2 mapped sources, 6 eligible each, different quantities -> [3, 3] quotas."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    order = OrderInput(
        order_id="EX-M2-DIFFERENT-QUANTITIES",
        line_items=[
            OrderLineInput("SRC-01", quantity=1, line_index=0),
            OrderLineInput("SRC-02", quantity=25, line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert_trace_counters_reconcile(result)
    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert result.unfilled_slots == 0

    src01, src02 = result.trace.source_evaluations
    assert src01.product_code == "SRC-01"
    assert src01.quantity_sum == 1
    assert src01.source_priority_position == 1
    assert src01.priority_key_used == [-6, 0, "SRC-01"]
    assert src01.initial_quota == 3
    assert src01.allocation_candidates_examined == 3
    assert src01.initial_allocation_selected_count == 3
    assert src01.backfill_selected_count == 0
    assert src01.unvisited_candidate_count == 3
    assert src01.selected_skus == ["SRC-01-T1", "SRC-01-T2", "SRC-01-T3"]
    assert src01.unvisited_skus == ["SRC-01-T4", "SRC-01-T5", "SRC-01-T6"]

    assert src02.product_code == "SRC-02"
    assert src02.quantity_sum == 25
    assert src02.source_priority_position == 2
    assert src02.priority_key_used == [-6, 1, "SRC-02"]
    assert src02.initial_quota == 3
    assert src02.allocation_candidates_examined == 3
    assert src02.initial_allocation_selected_count == 3
    assert src02.backfill_selected_count == 0
    assert src02.unvisited_candidate_count == 3
    assert src02.selected_skus == ["SRC-02-T1", "SRC-02-T2", "SRC-02-T3"]
    assert src02.unvisited_skus == ["SRC-02-T4", "SRC-02-T5", "SRC-02-T6"]


def test_stage4_1_postgresql_end_to_end_order_and_batch_query_integration(
    db_session: Session,
) -> None:
    """Verify end-to-end PostgreSQL integration: order loading, batch mapping/catalogue retrieval, fail-closed workbook-only, and synthetic metadata run."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)

    # 1. Seed workbook-only data (unknown metadata: is_rx=None, is_active=None, available_quantity=None)
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )

    # 2. Persist a Customer and a Delivered Order with 2 line items in PostgreSQL
    customer_repo = CustomerRepository(db_session)
    order_repo = OrderRepository(db_session)

    customer = customer_repo.create(
        CustomerCreate(
            external_customer_id="CUST-PG-STAGE41",
            phone_number="+919876543210",
            full_name="Aarav Sharma",
            whatsapp_opt_in=True,
        )
    )
    persisted_order = order_repo.create(
        OrderCreate(
            external_order_id="ORD-PG-STAGE41-001",
            customer_id=customer.id,
            order_status=OrderStatus.DELIVERED,
            line_items=[
                OrderLineItemCreate(
                    line_index=0,
                    product_code="SAMPLE-001",
                    product_name="Sample Product 001",
                    quantity=1,
                ),
                OrderLineItemCreate(
                    line_index=1,
                    product_code="SAMPLE-008",
                    product_name="Sample Product 008",
                    quantity=10,
                ),
            ],
        )
    )
    assert persisted_order.external_order_id == "ORD-PG-STAGE41-001"
    assert len(persisted_order.line_items) == 2

    db_service = DatabaseRecommendationService(db_session)

    # 3. Verify fail-closed behavior on workbook-only metadata via persisted Order lookup
    fail_closed_result = db_service.generate_for_external_order_id("ORD-PG-STAGE41-001")
    assert_trace_counters_reconcile(fail_closed_result)
    assert fail_closed_result.status == RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS
    assert fail_closed_result.recommendations == []
    assert fail_closed_result.unfilled_slots == 6
    for src_trace in fail_closed_result.trace.source_evaluations:
        assert src_trace.mapping_count == 6
        assert src_trace.eligibility_evaluations_count == 6
        assert src_trace.eligible_candidate_count == 0
        assert src_trace.rejected_candidate_count == 6
        assert src_trace.allocation_candidates_examined == 6
        assert src_trace.unvisited_candidate_count == 0
    for cand_trace in fail_closed_result.trace.candidate_evaluations:
        assert cand_trace.is_eligible is False
        assert CandidateExclusionReason.CATALOGUE_METADATA_MISSING in cand_trace.eligibility_rejection_reasons
        assert cand_trace.allocation_decision == CandidateAllocationDecision.SKIPPED_INELIGIBLE

    # 4. Apply synthetic demonstration catalogue metadata in PostgreSQL and expire session cache
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )
    db_session.expire_all()

    # 5. Capture actual SQL statements executed during generate_for_external_order_id
    executed_sql: list[str] = []

    def _capture_sql(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        stmt_clean = " ".join(statement.strip().split())
        if stmt_clean.upper().startswith("SELECT"):
            executed_sql.append(stmt_clean)

    engine_bind = db_session.get_bind()
    event.listen(engine_bind, "before_cursor_execute", _capture_sql)
    try:
        live_result = db_service.generate_for_external_order_id("ORD-PG-STAGE41-001")
    finally:
        event.remove(engine_bind, "before_cursor_execute", _capture_sql)

    assert_trace_counters_reconcile(live_result)
    assert live_result.status == RecommendationResultStatus.SUCCESS
    assert len(live_result.recommendations) == 6
    assert live_result.trace.ordered_recommended_skus == [
        "SAMPLE-002",
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-010",
        "SAMPLE-011",
        "SAMPLE-012",
    ]

    # Verify batch SQL queries (no N+1 per-product or per-mapping queries):
    # Query 1: SELECT orders ... WHERE external_order_id = ...
    # Query 2: SELECT order_line_items ... WHERE order_id IN (...)
    # Query 3: SELECT customers ... WHERE id IN (...)
    # Query 4: SELECT product_mappings ... WHERE source_product_code IN (...)
    # Query 5: SELECT products ... WHERE product_code IN (...)
    mapping_queries = [q for q in executed_sql if " FROM product_mappings " in q]
    product_queries = [q for q in executed_sql if " FROM products " in q]
    assert len(mapping_queries) == 1
    assert "IN (" in mapping_queries[0]
    assert len(product_queries) == 1
    assert "IN (" in product_queries[0]


def test_stage4_1_reconciliation_across_m7_duplicates_and_unmapped_sources() -> None:
    """Verify trace counter reconciliation across M>6 unvisited sources, cross-source duplicates, and unmapped sources."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=7)

    # 1. M=7 order: 7th source (SRC-06) gets initial_quota=0 and is never visited in backfill
    order_m7 = OrderInput(
        order_id="EX-M7-RECONCILE",
        line_items=[
            OrderLineInput("SRC-01", quantity=1, line_index=0),
            OrderLineInput("SRC-02", quantity=1, line_index=1),
            OrderLineInput("SRC-03", quantity=1, line_index=2),
            OrderLineInput("SRC-04", quantity=1, line_index=3),
            OrderLineInput("SRC-05", quantity=1, line_index=4),
            OrderLineInput("SRC-06", quantity=1, line_index=5),
            OrderLineInput("SRC-07", quantity=5, line_index=6),
            OrderLineInput("UNMAPPED-999", quantity=2, line_index=7),
        ],
    )
    res_m7 = recommendation_engine.generate(order=order_m7, mappings=mappings, catalogue=catalogue)
    assert_trace_counters_reconcile(res_m7)

    src06_trace = next(s for s in res_m7.trace.source_evaluations if s.product_code == "SRC-06")
    assert src06_trace.mapping_count == 6
    assert src06_trace.eligibility_evaluations_count == 6
    assert src06_trace.eligible_candidate_count == 6
    assert src06_trace.initial_quota == 0
    assert src06_trace.allocation_candidates_examined == 0
    assert src06_trace.unvisited_candidate_count == 6
    assert len(src06_trace.unvisited_skus) == 6

    unmapped_trace = next(
        s for s in res_m7.trace.source_evaluations if s.product_code == "UNMAPPED-999"
    )
    assert unmapped_trace.mapping_found is False
    assert unmapped_trace.mapping_count == 0
    assert unmapped_trace.eligibility_evaluations_count == 0
    assert unmapped_trace.allocation_candidates_examined == 0
    assert unmapped_trace.unvisited_candidate_count == 0

    # 2. Cross-source duplicate scenario: verify duplicate_candidate_count and SKIPPED_DUPLICATE
    dup_cat = {
        "SRC-A": make_catalogue_item("SRC-A"),
        "SRC-B": make_catalogue_item("SRC-B"),
        "SHARED-1": make_catalogue_item("SHARED-1"),
    }
    for code in ["A-2", "A-3", "A-4", "A-5", "A-6", "B-2", "B-3", "B-4", "B-5", "B-6"]:
        dup_cat[code] = make_catalogue_item(code)

    dup_mappings = [
        *make_six_mappings("SRC-A", ["SHARED-1", "A-2", "A-3", "A-4", "A-5", "A-6"]),
        *make_six_mappings("SRC-B", ["SHARED-1", "B-2", "B-3", "B-4", "B-5", "B-6"]),
    ]
    res_dup = recommendation_engine.generate(
        order=OrderInput(
            order_id="EX-DUP-RECONCILE",
            line_items=[OrderLineInput("SRC-A", 1, 0), OrderLineInput("SRC-B", 1, 1)],
        ),
        mappings=dup_mappings,
        catalogue=dup_cat,
    )
    assert_trace_counters_reconcile(res_dup)

    src_b_trace = next(s for s in res_dup.trace.source_evaluations if s.product_code == "SRC-B")
    assert src_b_trace.mapping_count == 6
    assert src_b_trace.eligibility_evaluations_count == 6
    assert src_b_trace.eligible_candidate_count == 6
    assert src_b_trace.rejected_candidate_count == 0
    assert src_b_trace.allocation_candidates_examined == 4  # SHARED-1 (dup) + B-2, B-3, B-4 (selected)
    assert src_b_trace.initial_allocation_selected_count == 3
    assert src_b_trace.backfill_selected_count == 0
    assert src_b_trace.duplicate_candidate_count == 1
    assert src_b_trace.duplicate_skus == ["SHARED-1"]
    assert src_b_trace.unvisited_candidate_count == 2
    assert src_b_trace.unvisited_skus == ["B-5", "B-6"]

