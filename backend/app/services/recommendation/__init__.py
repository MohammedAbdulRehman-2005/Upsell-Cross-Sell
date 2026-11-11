"""Export deterministic recommendation engine components."""
from __future__ import annotations

from app.services.recommendation.allocator import (
    RecommendationInputError,
    SourceAllocationPlan,
    allocate_initial_quotas,
    compute_source_priority_key,
    normalize_order_lines,
)
from app.services.recommendation.eligibility import (
    EligibilityCheckResult,
    evaluate_candidate_eligibility,
)
from app.services.recommendation.engine import (
    DatabaseRecommendationService,
    RecommendationEngine,
    recommendation_engine,
)
from app.services.recommendation.models import (
    CandidateAllocationDecision,
    CandidateExclusionReason,
    CandidateTraceEntry,
    CatalogueItemInput,
    DecisionTrace,
    MAX_RECOMMENDATIONS,
    MappingCandidateInput,
    NormalizedOrderSource,
    OrderInput,
    OrderLineInput,
    OrderSummaryTrace,
    RecommendationResult,
    RecommendationResultStatus,
    SelectedRecommendation,
    SelectionPhase,
    SourceTraceEntry,
)

__all__ = [
    "CandidateAllocationDecision",
    "CandidateExclusionReason",
    "CandidateTraceEntry",
    "CatalogueItemInput",
    "DatabaseRecommendationService",
    "DecisionTrace",
    "EligibilityCheckResult",
    "MAX_RECOMMENDATIONS",
    "MappingCandidateInput",
    "NormalizedOrderSource",
    "OrderInput",
    "OrderLineInput",
    "OrderSummaryTrace",
    "RecommendationEngine",
    "RecommendationInputError",
    "RecommendationResult",
    "RecommendationResultStatus",
    "SelectedRecommendation",
    "SelectionPhase",
    "SourceAllocationPlan",
    "SourceTraceEntry",
    "allocate_initial_quotas",
    "compute_source_priority_key",
    "evaluate_candidate_eligibility",
    "normalize_order_lines",
    "recommendation_engine",
]
