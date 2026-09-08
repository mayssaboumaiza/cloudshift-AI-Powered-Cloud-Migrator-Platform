"""
test_ahp_weights.py — Unit tests for the AHP weight derivation module.

Tests verify:
  1. CR < 0.10 (matrix is consistent by Saaty's criterion)
  2. sum(weights) == 1.0 (proper probability distribution)
  3. Weight ordering: equivalence > maturity > budget_fit > region_fit > complexity
  4. All weights are positive
  5. AHP_SOURCES has an entry for each criterion
"""

import pytest
from core.ahp_weights import (
    AHP_WEIGHTS,
    AHP_CONSISTENCY_RATIO,
    AHP_SOURCES,
    validate_weights,
)


def test_consistency_ratio_below_threshold():
    """Saaty criterion: CR < 0.10 for an acceptably consistent matrix."""
    assert AHP_CONSISTENCY_RATIO < 0.10, (
        f"CR={AHP_CONSISTENCY_RATIO:.4f} >= 0.10 — the pairwise matrix is inconsistent. "
        "Review the comparison values in core/ahp_weights.py."
    )


def test_weights_sum_to_one():
    """SAW requirement: weights must sum to exactly 1.0."""
    total = sum(AHP_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-4, f"sum(weights)={total:.8f} != 1.0"


def test_all_weights_positive():
    """Every weight must be strictly positive."""
    for criterion, w in AHP_WEIGHTS.items():
        assert w > 0, f"weight[{criterion}]={w} is not positive"


def test_weight_ordering():
    """Verify the expected dominance order derived from the AHP matrix.

    equivalence > maturity > budget_fit > region_fit > complexity
    This reflects: functional fit > provider maturity > cost > geography > effort
    """
    w = AHP_WEIGHTS
    assert w["equivalence"] > w["maturity"],   "equivalence should outweigh maturity"
    assert w["maturity"]    > w["budget_fit"],  "maturity should outweigh budget_fit"
    assert w["budget_fit"]  > w["region_fit"],  "budget_fit should outweigh region_fit"
    assert w["region_fit"]  > w["complexity"],  "region_fit should outweigh complexity"


def test_equivalence_is_dominant_criterion():
    """Equivalence weight should be >= 0.40 (largest single criterion)."""
    assert AHP_WEIGHTS["equivalence"] >= 0.40, (
        f"equivalence weight {AHP_WEIGHTS['equivalence']:.4f} < 0.40 — "
        "AWS MAP Framework requires functional fit as primary criterion"
    )


def test_sources_cover_all_criteria():
    """Every weight must have a documented academic/industry source."""
    missing = [k for k in AHP_WEIGHTS if k not in AHP_SOURCES]
    assert not missing, f"Missing AHP_SOURCES entries for: {missing}"


def test_validate_weights_helper():
    """validate_weights() must return a passing report."""
    report = validate_weights()
    assert report["sum_ok"],  f"sum not OK: {report['weight_sum']}"
    assert report["cr_ok"],   f"CR not OK: {report['consistency_ratio']}"
    assert report["n"] == 5, f"Expected 5 criteria, got {report['n']}"
