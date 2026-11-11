"""Pure deterministic recommendation engine and database integration service."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.services.recommendation.allocator import (
    RecommendationInputError,
    allocate_initial_quotas,
    normalize_order_lines,
)
from app.services.recommendation.eligibility import evaluate_candidate_eligibility
from app.services.recommendation.models import (
    CandidateAllocationDecision,
    CandidateExclusionReason,
    CandidateTraceEntry,
    CatalogueItemInput,
    MAX_RECOMMENDATIONS,
    MappingCandidateInput,
    NormalizedOrderSource,
    OrderInput,
    OrderLineInput,
    RecommendationResult,
    SelectedRecommendation,
    SelectionPhase,
    SourceTraceEntry,
)
from app.services.recommendation.trace import (
    assemble_decision_trace,
    build_order_summary_trace,
    resolve_result_status,
)


def _index_catalogue(
    catalogue: Sequence[CatalogueItemInput] | Mapping[str, CatalogueItemInput],
) -> dict[str, CatalogueItemInput]:
    """Normalize catalogue input into a dictionary keyed by `product_code`."""
    if isinstance(catalogue, Mapping):
        return dict(catalogue)
    indexed: dict[str, CatalogueItemInput] = {}
    for item in catalogue:
        code = item.product_code.strip()
        if code in indexed:
            raise RecommendationInputError(f"Duplicate product_code '{code}' in catalogue input")
        indexed[code] = item
    return indexed


def _index_mappings_by_source(
    mappings: Sequence[MappingCandidateInput] | Mapping[str, Sequence[MappingCandidateInput]],
) -> dict[str, list[MappingCandidateInput]]:
    """Validate and group mapping records by `source_product_code` ordered by ascending rank (1..6)."""
    grouped: dict[str, list[MappingCandidateInput]] = {}
    if isinstance(mappings, Mapping):
        raw_iterable = [item for group in mappings.values() for item in group]
    else:
        raw_iterable = list(mappings)

    seen_rank_by_source: dict[str, set[int]] = {}
    seen_target_by_source: dict[str, set[str]] = {}

    for item in raw_iterable:
        src = (item.source_product_code or "").strip()
        tgt = (item.recommended_product_code or "").strip()
        rank = item.recommendation_rank
        if not src or not tgt:
            raise RecommendationInputError("Mapping record has blank source or recommended product_code")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1 or rank > 6:
            raise RecommendationInputError(
                f"Mapping for source '{src}' target '{tgt}' has invalid rank {rank!r} (expected 1..6)"
            )
        if src == tgt:
            raise RecommendationInputError(
                f"Self-recommendation detected in mapping input for '{src}' at rank {rank}"
            )
        ranks = seen_rank_by_source.setdefault(src, set())
        if rank in ranks:
            raise RecommendationInputError(
                f"Duplicate mapping rank {rank} for source '{src}'"
            )
        ranks.add(rank)

        targets = seen_target_by_source.setdefault(src, set())
        if tgt in targets:
            raise RecommendationInputError(
                f"Duplicate recommended target '{tgt}' for source '{src}'"
            )
        targets.add(tgt)

        grouped.setdefault(src, []).append(item)

    for src, items in grouped.items():
        items.sort(key=lambda m: m.recommendation_rank)

    return grouped


class RecommendationEngine:
    """Pure, stateless deterministic recommendation engine (Stage 4).

    Independent of FastAPI, SQLAlchemy sessions, HTTP clients, and LLMs.
    """

    def generate(
        self,
        *,
        order: OrderInput,
        mappings: Sequence[MappingCandidateInput] | Mapping[str, Sequence[MappingCandidateInput]],
        catalogue: Sequence[CatalogueItemInput] | Mapping[str, CatalogueItemInput],
    ) -> RecommendationResult:
        """Execute the 8-step deterministic recommendation algorithm and return result + trace."""
        # Step 1: Normalize order lines & build purchased exclusion set
        normalized_sources, purchased_skus = normalize_order_lines(order)
        catalogue_by_code = _index_catalogue(catalogue)
        mappings_by_source = _index_mappings_by_source(mappings)

        mapped_sources: list[NormalizedOrderSource] = []
        unmapped_sources: list[NormalizedOrderSource] = []
        for src in normalized_sources:
            src_mappings = mappings_by_source.get(src.product_code, [])
            if src_mappings:
                mapped_sources.append(src)
            else:
                unmapped_sources.append(src)

        # Step 2: Evaluate all candidates for all mapped sources before prioritization
        candidate_traces_by_source: dict[str, list[CandidateTraceEntry]] = {}
        eligible_counts_by_source: dict[str, int] = {}

        for src in mapped_sources:
            src_code = src.product_code
            src_mappings = mappings_by_source[src_code]
            traces: list[CandidateTraceEntry] = []
            eligible_count = 0

            for mapping_entry in src_mappings:
                eval_res = evaluate_candidate_eligibility(
                    candidate_code=mapping_entry.recommended_product_code,
                    fallback_candidate_name=mapping_entry.recommended_product_name,
                    purchased_sku_set=purchased_skus,
                    catalogue_by_code=catalogue_by_code,
                )
                if eval_res.is_eligible:
                    eligible_count += 1

                cand_cat = catalogue_by_code.get(mapping_entry.recommended_product_code)
                traces.append(
                    CandidateTraceEntry(
                        candidate_product_code=mapping_entry.recommended_product_code,
                        candidate_product_name=eval_res.resolved_product_name,
                        source_product_code=src_code,
                        original_mapping_rank=mapping_entry.recommendation_rank,
                        is_eligible=eval_res.is_eligible,
                        metadata_source=cand_cat.metadata_source if cand_cat is not None else None,
                        is_synthetic_metadata=(
                            bool(cand_cat.is_synthetic_metadata) if cand_cat is not None else False
                        ),
                        eligibility_rejection_reasons=list(eval_res.rejection_reasons),
                        rejection_reasons=list(eval_res.rejection_reasons),
                        excluded_as_purchased=eval_res.excluded_as_purchased,
                        examined_during_allocation=False,
                        allocation_decision=CandidateAllocationDecision.NOT_REACHED,
                        excluded_as_duplicate=False,
                        selected=False,
                        selection_phase=None,
                        selection_sequence=None,
                    )
                )

            candidate_traces_by_source[src_code] = traces
            eligible_counts_by_source[src_code] = eligible_count

        # Step 3: Prioritize mapped sources & allocate initial quotas
        allocation_plans = allocate_initial_quotas(mapped_sources, eligible_counts_by_source)
        plan_by_source = {plan.product_code: plan for plan in allocation_plans}
        source_by_code = {src.product_code: src for src in normalized_sources}

        # Cursors track the next candidate index (0..len-1) to inspect for each prioritized source
        cursors: dict[str, int] = {plan.product_code: 0 for plan in allocation_plans}
        selections: list[SelectedRecommendation] = []
        retained_by_target_code: dict[str, SelectedRecommendation] = {}

        def _resolve_source_name(source_code: str) -> str:
            cat = catalogue_by_code.get(source_code)
            if cat is not None and cat.product_name:
                return cat.product_name
            src_obj = source_by_code.get(source_code)
            if src_obj is not None and src_obj.product_name:
                return src_obj.product_name
            return source_code

        def _accept_candidate(
            candidate_trace: CandidateTraceEntry,
            phase: SelectionPhase,
        ) -> SelectedRecommendation:
            seq = len(selections) + 1
            cat_item = catalogue_by_code.get(candidate_trace.candidate_product_code)
            unit_price_str = (
                f"{cat_item.unit_price:.2f}"
                if cat_item is not None and cat_item.unit_price is not None
                else None
            )
            currency_str = cat_item.currency if cat_item is not None else "INR"

            selected_rec = SelectedRecommendation(
                selection_sequence=seq,
                recommended_product_code=candidate_trace.candidate_product_code,
                recommended_product_name=candidate_trace.candidate_product_name,
                source_product_code=candidate_trace.source_product_code,
                source_product_name=_resolve_source_name(candidate_trace.source_product_code),
                original_mapping_rank=candidate_trace.original_mapping_rank,
                selection_phase=phase,
                unit_price=unit_price_str,
                currency=currency_str,
                metadata_source=(
                    cat_item.metadata_source if cat_item is not None else "WORKBOOK_ONLY"
                ),
                is_synthetic_metadata=(
                    bool(cat_item.is_synthetic_metadata) if cat_item is not None else False
                ),
            )
            candidate_trace.selected = True
            candidate_trace.selection_phase = phase
            candidate_trace.selection_sequence = seq
            candidate_trace.allocation_decision = (
                CandidateAllocationDecision.SELECTED_INITIAL_ALLOCATION
                if phase == SelectionPhase.INITIAL_ALLOCATION
                else CandidateAllocationDecision.SELECTED_BACKFILL
            )
            selections.append(selected_rec)
            retained_by_target_code[selected_rec.recommended_product_code] = selected_rec
            return selected_rec

        def _mark_duplicate_if_already_selected(candidate_trace: CandidateTraceEntry) -> bool:
            existing_selection = retained_by_target_code.get(candidate_trace.candidate_product_code)
            if existing_selection is None:
                return False
            candidate_trace.excluded_as_duplicate = True
            candidate_trace.allocation_decision = CandidateAllocationDecision.SKIPPED_DUPLICATE
            if CandidateExclusionReason.DUPLICATE_RECOMMENDATION not in candidate_trace.rejection_reasons:
                candidate_trace.rejection_reasons.append(
                    CandidateExclusionReason.DUPLICATE_RECOMMENDATION
                )
            candidate_trace.duplicate_retained_from_source = existing_selection.source_product_code
            candidate_trace.duplicate_retained_rank = existing_selection.original_mapping_rank
            candidate_trace.duplicate_retained_sequence = existing_selection.selection_sequence
            return True

        # Step 4: Phase 1 — Initial quota fill (INITIAL_ALLOCATION)
        for plan in allocation_plans:
            if len(selections) >= MAX_RECOMMENDATIONS:
                break
            if plan.initial_quota <= 0:
                continue

            src_code = plan.product_code
            traces = candidate_traces_by_source[src_code]
            filled_for_source = 0

            while (
                cursors[src_code] < len(traces)
                and filled_for_source < plan.initial_quota
                and len(selections) < MAX_RECOMMENDATIONS
            ):
                candidate_trace = traces[cursors[src_code]]
                cursors[src_code] += 1
                candidate_trace.examined_during_allocation = True

                if not candidate_trace.is_eligible:
                    candidate_trace.allocation_decision = (
                        CandidateAllocationDecision.SKIPPED_INELIGIBLE
                    )
                    continue

                if _mark_duplicate_if_already_selected(candidate_trace):
                    continue

                _accept_candidate(candidate_trace, SelectionPhase.INITIAL_ALLOCATION)
                filled_for_source += 1

        # Step 5: Phase 2 — Deterministic round-robin backfill across ALL M mapped sources (BACKFILL)
        # Always starts from the first source in priority order (index 0) on every round.
        while len(selections) < MAX_RECOMMENDATIONS and allocation_plans:
            made_progress_in_round = False
            for plan in allocation_plans:
                if len(selections) >= MAX_RECOMMENDATIONS:
                    break
                src_code = plan.product_code
                traces = candidate_traces_by_source[src_code]

                while cursors[src_code] < len(traces):
                    candidate_trace = traces[cursors[src_code]]
                    cursors[src_code] += 1
                    candidate_trace.examined_during_allocation = True

                    if not candidate_trace.is_eligible:
                        candidate_trace.allocation_decision = (
                            CandidateAllocationDecision.SKIPPED_INELIGIBLE
                        )
                        continue

                    if _mark_duplicate_if_already_selected(candidate_trace):
                        continue

                    _accept_candidate(candidate_trace, SelectionPhase.BACKFILL)
                    made_progress_in_round = True
                    break

            if not made_progress_in_round:
                break

        # Step 6: Build source and candidate trace entries in deterministic order
        source_evaluations: list[SourceTraceEntry] = []
        ordered_candidate_traces: list[CandidateTraceEntry] = []

        # Include prioritized mapped sources first (in priority order 1..M)
        for plan in allocation_plans:
            src_code = plan.product_code
            src_obj = source_by_code[src_code]
            traces = candidate_traces_by_source[src_code]
            ordered_candidate_traces.extend(traces)

            mapping_count = len(traces)
            eligible_count = eligible_counts_by_source[src_code]
            rejected_skus = [t.candidate_product_code for t in traces if not t.is_eligible]
            selected_skus = [t.candidate_product_code for t in traces if t.selected]
            duplicate_skus = [
                t.candidate_product_code for t in traces if t.excluded_as_duplicate
            ]
            unvisited_skus = [
                t.candidate_product_code for t in traces if not t.examined_during_allocation
            ]
            initial_selected_count = sum(
                1
                for t in traces
                if t.selected and t.selection_phase == SelectionPhase.INITIAL_ALLOCATION
            )
            backfill_selected_count = sum(
                1
                for t in traces
                if t.selected and t.selection_phase == SelectionPhase.BACKFILL
            )
            allocation_examined_count = sum(
                1 for t in traces if t.examined_during_allocation
            )

            src_cat = catalogue_by_code.get(src_code)
            source_evaluations.append(
                SourceTraceEntry(
                    product_code=src_code,
                    product_name=_resolve_source_name(src_code),
                    first_line_index=src_obj.first_line_index,
                    quantity_sum=src_obj.quantity_sum,
                    mapping_found=True,
                    status_reason=(
                        "ELIGIBLE_CANDIDATES_AVAILABLE"
                        if eligible_count > 0
                        else "NO_ELIGIBLE_CANDIDATES"
                    ),
                    mapping_count=mapping_count,
                    eligibility_evaluations_count=mapping_count,
                    eligible_candidate_count=eligible_count,
                    rejected_candidate_count=len(rejected_skus),
                    source_priority_position=plan.priority_position,
                    priority_key_used=list(plan.priority_key),
                    initial_quota=plan.initial_quota,
                    allocation_candidates_examined=allocation_examined_count,
                    initial_allocation_selected_count=initial_selected_count,
                    backfill_selected_count=backfill_selected_count,
                    candidates_selected=len(selected_skus),
                    duplicate_candidate_count=len(duplicate_skus),
                    unvisited_candidate_count=len(unvisited_skus),
                    metadata_source=src_cat.metadata_source if src_cat is not None else None,
                    is_synthetic_metadata=(
                        bool(src_cat.is_synthetic_metadata) if src_cat is not None else False
                    ),
                    selected_skus=selected_skus,
                    rejected_skus=rejected_skus,
                    duplicate_skus=duplicate_skus,
                    unvisited_skus=unvisited_skus,
                )
            )

        # Include unmapped sources (in first_line_index order) with NO_MAPPING_FOUND
        for src_obj in unmapped_sources:
            src_code = src_obj.product_code
            src_cat = catalogue_by_code.get(src_code)
            source_evaluations.append(
                SourceTraceEntry(
                    product_code=src_code,
                    product_name=_resolve_source_name(src_code),
                    first_line_index=src_obj.first_line_index,
                    quantity_sum=src_obj.quantity_sum,
                    mapping_found=False,
                    status_reason=CandidateExclusionReason.NO_MAPPING_FOUND.value,
                    mapping_count=0,
                    eligibility_evaluations_count=0,
                    eligible_candidate_count=0,
                    rejected_candidate_count=0,
                    source_priority_position=None,
                    priority_key_used=None,
                    initial_quota=0,
                    allocation_candidates_examined=0,
                    initial_allocation_selected_count=0,
                    backfill_selected_count=0,
                    candidates_selected=0,
                    duplicate_candidate_count=0,
                    unvisited_candidate_count=0,
                    metadata_source=src_cat.metadata_source if src_cat is not None else None,
                    is_synthetic_metadata=(
                        bool(src_cat.is_synthetic_metadata) if src_cat is not None else False
                    ),
                    selected_skus=[],
                    rejected_skus=[],
                    duplicate_skus=[],
                    unvisited_skus=[],
                )
            )

        referenced_codes = {s.product_code for s in normalized_sources} | {
            c.candidate_product_code for c in ordered_candidate_traces
        }
        metadata_sources_used = sorted(
            {
                catalogue_by_code[code].metadata_source
                for code in referenced_codes
                if code in catalogue_by_code and catalogue_by_code[code].metadata_source
            }
        )
        contains_synthetic = any(
            bool(catalogue_by_code[code].is_synthetic_metadata)
            for code in referenced_codes
            if code in catalogue_by_code
        )

        order_summary = build_order_summary_trace(
            order_id=order.order_id,
            original_line_count=len(order.line_items),
            normalized_sources=normalized_sources,
            mapped_source_count=len(mapped_sources),
            purchased_skus=purchased_skus,
            catalogue_metadata_sources_used=metadata_sources_used,
            contains_synthetic_metadata=contains_synthetic,
        )
        status = resolve_result_status(
            mapped_source_count=len(mapped_sources),
            selected_count=len(selections),
        )
        trace = assemble_decision_trace(
            order_summary=order_summary,
            source_evaluations=source_evaluations,
            candidate_evaluations=ordered_candidate_traces,
            selections=selections,
            status=status,
        )

        return RecommendationResult(
            order_id=order.order_id,
            status=status,
            recommendations=selections,
            unfilled_slots=trace.unfilled_recommendation_slots,
            trace=trace,
        )


class DatabaseRecommendationService:
    """Thin database adapter that batch-loads mappings and catalogue metadata for `RecommendationEngine`."""

    def __init__(
        self,
        session: Session,
        engine: RecommendationEngine | None = None,
    ) -> None:
        self.session = session
        self.product_repo = ProductRepository(session)
        self.order_repo = OrderRepository(session)
        self.engine = engine or RecommendationEngine()

    def generate_for_order_input(self, order: OrderInput) -> RecommendationResult:
        """Batch-fetch mappings and catalogue metadata from PostgreSQL and run the pure engine."""
        normalized_sources, _ = normalize_order_lines(order)
        source_codes = [s.product_code for s in normalized_sources]

        # 1 batch query for all mappings of ordered source SKUs
        db_mappings = self.product_repo.list_mappings_for_sources(source_codes)
        target_codes = {m.recommended_product_code for m in db_mappings}
        all_needed_codes = sorted(set(source_codes) | target_codes)

        # 1 batch query for all source and target product catalogue records
        db_products = self.product_repo.get_by_codes(all_needed_codes)

        catalogue_inputs: dict[str, CatalogueItemInput] = {
            p.product_code: CatalogueItemInput(
                product_code=p.product_code,
                product_name=p.product_name,
                is_rx=p.is_rx,
                is_active=p.is_active,
                available_quantity=p.available_quantity,
                unit_price=p.unit_price,
                currency=p.currency,
                image_url=p.image_url,
                product_url=p.product_url,
                metadata_source=str(p.metadata_source),
                is_synthetic_metadata=bool(p.is_synthetic_metadata),
            )
            for p in db_products
        }

        mapping_inputs = [
            MappingCandidateInput(
                source_product_code=m.source_product_code,
                recommended_product_code=m.recommended_product_code,
                recommendation_rank=m.recommendation_rank,
                recommended_product_name=(
                    catalogue_inputs[m.recommended_product_code].product_name
                    if m.recommended_product_code in catalogue_inputs
                    else None
                ),
            )
            for m in db_mappings
        ]

        return self.engine.generate(
            order=order,
            mappings=mapping_inputs,
            catalogue=catalogue_inputs,
        )

    def generate_for_external_order_id(self, external_order_id: str) -> RecommendationResult:
        """Load a persisted `Order` by `external_order_id` and generate recommendations."""
        db_order = self.order_repo.get_by_external_id(external_order_id)
        if db_order is None:
            raise ValueError(f"Order '{external_order_id}' not found")

        order_input = OrderInput(
            order_id=db_order.external_order_id,
            line_items=[
                OrderLineInput(
                    product_code=li.product_code,
                    quantity=li.quantity,
                    line_index=li.line_index,
                    product_name=li.product_name,
                )
                for li in db_order.line_items
            ],
        )
        return self.generate_for_order_input(order_input)


recommendation_engine = RecommendationEngine()
