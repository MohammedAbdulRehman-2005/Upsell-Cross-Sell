"""Tests for strict determinism and canonical JSON reproducibility (Scenarios 39, 40)."""
from __future__ import annotations

import random

from app.services.recommendation import (
    OrderInput,
    OrderLineInput,
    recommendation_engine,
)
from tests.recommendation import build_independent_clusters_fixture


def test_scenario_39_identical_input_produces_identical_canonical_json_output() -> None:
    """Scenario 39: Identical inputs produce byte-for-byte identical JSON outputs."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=8)
    # Create an order with 7 sources, duplicate lines, and some ineligible candidates
    catalogue["SRC-01-T1"] = catalogue["SRC-01-T1"].__class__(
        product_code="SRC-01-T1",
        product_name="Product SRC-01-T1",
        is_rx=True,
        is_active=True,
        available_quantity=10,
    )

    order = OrderInput(
        order_id="ORD-S39-DETERMINISTIC",
        line_items=[
            OrderLineInput("SRC-01", quantity=1, line_index=0),
            OrderLineInput("SRC-02", quantity=2, line_index=1),
            OrderLineInput("SRC-03", quantity=5, line_index=2),
            OrderLineInput("SRC-01", quantity=3, line_index=3),
            OrderLineInput("SRC-04", quantity=1, line_index=4),
            OrderLineInput("SRC-05", quantity=4, line_index=5),
            OrderLineInput("SRC-06", quantity=2, line_index=6),
            OrderLineInput("SRC-07", quantity=6, line_index=7),
        ],
    )

    result_a = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)
    result_b = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result_a.to_canonical_json() == result_b.to_canonical_json()


def test_scenario_40_repeated_runs_and_permuted_mapping_inputs_produce_identical_results() -> None:
    """Scenario 40: 50 repeated runs with permuted mapping list order produce identical recommendation order and trace."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=6)
    order = OrderInput(
        order_id="ORD-S40-REPEAT",
        line_items=[
            OrderLineInput("SRC-04", quantity=2, line_index=0),
            OrderLineInput("SRC-01", quantity=1, line_index=1),
            OrderLineInput("SRC-02", quantity=3, line_index=2),
            OrderLineInput("SRC-03", quantity=1, line_index=3),
        ],
    )

    baseline = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)
    baseline_json = baseline.to_canonical_json()

    rng = random.Random(42)
    for _ in range(50):
        shuffled_mappings = list(mappings)
        rng.shuffle(shuffled_mappings)
        shuffled_catalogue_keys = list(catalogue.keys())
        rng.shuffle(shuffled_catalogue_keys)
        reordered_catalogue = {k: catalogue[k] for k in shuffled_catalogue_keys}

        run_result = recommendation_engine.generate(
            order=order,
            mappings=shuffled_mappings,
            catalogue=reordered_catalogue,
        )
        assert run_result.to_canonical_json() == baseline_json
