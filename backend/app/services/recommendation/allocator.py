"""Order normalization, conditional source prioritization, equal quota allocation, and backfill."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.recommendation.models import (
    MAX_RECOMMENDATIONS,
    NormalizedOrderSource,
    OrderInput,
)


class RecommendationInputError(ValueError):
    """Raised when order or mapping inputs violate structural preconditions."""


@dataclass(frozen=True)
class SourceAllocationPlan:
    """Priority rank, priority key, and initial slot quota for a mapped source product."""

    product_code: str
    priority_position: int
    priority_key: tuple[int | str, ...]
    initial_quota: int


def normalize_order_lines(order: OrderInput) -> tuple[list[NormalizedOrderSource], set[str]]:
    """Aggregate repeated order lines into distinct sources while preserving earliest line_index.

    Rule 2 & Rule 3:
    - Validates non-blank SKU codes and positive quantities (`>= 1`).
    - Resolves `line_index` (defaults to 0-based position if `None`, rejects duplicate explicit indices).
    - Orders lines by ascending `line_index`.
    - Aggregates repeated occurrences of the same `product_code` into one `NormalizedOrderSource`
      with `quantity_sum` and earliest `first_line_index`.
    - Returns `(normalized_sources_in_first_line_order, purchased_sku_set)`.
    """
    if not order.order_id or not order.order_id.strip():
        raise RecommendationInputError("order_id must be a non-empty string")

    resolved_lines: list[tuple[int, str, int, str | None]] = []
    seen_line_indices: set[int] = set()

    for idx, line in enumerate(order.line_items):
        code = (line.product_code or "").strip()
        if not code:
            raise RecommendationInputError(f"Order line at position {idx} has blank product_code")
        if isinstance(line.quantity, bool) or not isinstance(line.quantity, int) or line.quantity < 1:
            raise RecommendationInputError(
                f"Order line for '{code}' has invalid quantity {line.quantity!r} (must be int >= 1)"
            )
        line_index = line.line_index if line.line_index is not None else idx
        if line_index < 0:
            raise RecommendationInputError(
                f"Order line for '{code}' has negative line_index {line_index}"
            )
        if line_index in seen_line_indices:
            raise RecommendationInputError(
                f"Duplicate line_index {line_index} in order '{order.order_id}'"
            )
        seen_line_indices.add(line_index)
        resolved_lines.append((line_index, code, line.quantity, line.product_name))

    resolved_lines.sort(key=lambda item: item[0])

    aggregated: dict[str, NormalizedOrderSource] = {}
    for line_index, code, qty, name in resolved_lines:
        existing = aggregated.get(code)
        if existing is None:
            aggregated[code] = NormalizedOrderSource(
                product_code=code,
                product_name=name,
                quantity_sum=qty,
                first_line_index=line_index,
                occurrence_count=1,
            )
        else:
            aggregated[code] = NormalizedOrderSource(
                product_code=code,
                product_name=existing.product_name or name,
                quantity_sum=existing.quantity_sum + qty,
                first_line_index=min(existing.first_line_index, line_index),
                occurrence_count=existing.occurrence_count + 1,
            )

    normalized_sources = sorted(
        aggregated.values(),
        key=lambda src: (src.first_line_index, src.product_code),
    )
    purchased_skus = set(aggregated.keys())
    return normalized_sources, purchased_skus


def compute_source_priority_key(
    source: NormalizedOrderSource,
    *,
    eligible_candidate_count: int,
    mapped_source_count: int,
) -> tuple[int | str, ...]:
    """Compute the conditional deterministic priority key for a mapped source (Rule 7).

    Case A (`1 <= M <= 6`):
        `(-eligible_candidate_count, first_line_index, product_code)`
        Quantity is NOT included when `M <= 6`.

    Case B (`M > 6`):
        `(-eligible_candidate_count, -quantity_sum, first_line_index, product_code)`
        Quantity is used only as a tie-breaker when `M > 6`.
    """
    if mapped_source_count <= MAX_RECOMMENDATIONS:
        return (
            -eligible_candidate_count,
            source.first_line_index,
            source.product_code,
        )
    return (
        -eligible_candidate_count,
        -source.quantity_sum,
        source.first_line_index,
        source.product_code,
    )


def allocate_initial_quotas(
    mapped_sources: Sequence[NormalizedOrderSource],
    eligible_counts_by_source: dict[str, int],
) -> list[SourceAllocationPlan]:
    """Sort mapped sources by conditional priority key and assign initial slot quotas (Rule 7).

    - Let `M = len(mapped_sources)`.
    - If `M == 0`, returns `[]`.
    - If `1 <= M <= 6`:
      `base_quota = 6 // M`, `remainder = 6 % M`.
      Every source receives `base_quota`; the first `remainder` sources in priority order
      receive `+1` slot.
    - If `M > 6`:
      `base_quota = 0`, `remainder = 6`.
      The first 6 sources in priority order receive `1` initial slot each; remaining sources
      receive `0` initial slots and still participate in deterministic round-robin backfill.
    """
    m = len(mapped_sources)
    if m == 0:
        return []

    keyed_sources: list[tuple[tuple[int | str, ...], NormalizedOrderSource]] = []
    for src in mapped_sources:
        elig_count = eligible_counts_by_source.get(src.product_code, 0)
        pkey = compute_source_priority_key(
            src,
            eligible_candidate_count=elig_count,
            mapped_source_count=m,
        )
        keyed_sources.append((pkey, src))

    keyed_sources.sort(key=lambda pair: pair[0])

    base_quota = MAX_RECOMMENDATIONS // m
    remainder = MAX_RECOMMENDATIONS % m

    plans: list[SourceAllocationPlan] = []
    for priority_idx, (pkey, src) in enumerate(keyed_sources):
        initial_quota = base_quota + (1 if priority_idx < remainder else 0)
        plans.append(
            SourceAllocationPlan(
                product_code=src.product_code,
                priority_position=priority_idx + 1,
                priority_key=pkey,
                initial_quota=initial_quota,
            )
        )
    return plans
