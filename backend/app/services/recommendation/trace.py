"""Decision trace builder helpers for the deterministic recommendation engine."""
from __future__ import annotations

from app.services.recommendation.models import (
    CandidateTraceEntry,
    DecisionTrace,
    MAX_RECOMMENDATIONS,
    NormalizedOrderSource,
    OrderSummaryTrace,
    RecommendationResultStatus,
    SelectedRecommendation,
    SourceTraceEntry,
)


def build_order_summary_trace(
    *,
    order_id: str,
    original_line_count: int,
    normalized_sources: list[NormalizedOrderSource],
    mapped_source_count: int,
    purchased_skus: set[str],
    catalogue_metadata_sources_used: list[str] | None = None,
    contains_synthetic_metadata: bool = False,
) -> OrderSummaryTrace:
    """Create the order summary section of the decision trace."""
    return OrderSummaryTrace(
        order_id=order_id,
        original_order_line_count=original_line_count,
        distinct_source_sku_count=len(normalized_sources),
        mapped_source_sku_count=mapped_source_count,
        unmapped_source_sku_count=len(normalized_sources) - mapped_source_count,
        purchased_sku_set=sorted(purchased_skus),
        total_quantity_per_source={
            src.product_code: src.quantity_sum for src in normalized_sources
        },
        first_line_index_per_source={
            src.product_code: src.first_line_index for src in normalized_sources
        },
        catalogue_metadata_sources_used=list(catalogue_metadata_sources_used or []),
        contains_synthetic_metadata=contains_synthetic_metadata,
    )


def resolve_result_status(
    *,
    mapped_source_count: int,
    selected_count: int,
) -> RecommendationResultStatus:
    """Determine the final deterministic status code for the recommendation run."""
    if mapped_source_count == 0:
        return RecommendationResultStatus.NO_MAPPED_SOURCE_PRODUCTS
    if selected_count == 0:
        return RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS
    if selected_count == MAX_RECOMMENDATIONS:
        return RecommendationResultStatus.SUCCESS
    return RecommendationResultStatus.PARTIAL_SUCCESS


def assemble_decision_trace(
    *,
    order_summary: OrderSummaryTrace,
    source_evaluations: list[SourceTraceEntry],
    candidate_evaluations: list[CandidateTraceEntry],
    selections: list[SelectedRecommendation],
    status: RecommendationResultStatus,
) -> DecisionTrace:
    """Assemble the complete `DecisionTrace` object."""
    selected_count = len(selections)
    unfilled_slots = max(0, MAX_RECOMMENDATIONS - selected_count)
    return DecisionTrace(
        order_summary=order_summary,
        source_evaluations=source_evaluations,
        candidate_evaluations=candidate_evaluations,
        selected_recommendation_count=selected_count,
        ordered_recommended_skus=[item.recommended_product_code for item in selections],
        selection_provenance=list(selections),
        unfilled_recommendation_slots=unfilled_slots,
        final_result_status=status,
    )
