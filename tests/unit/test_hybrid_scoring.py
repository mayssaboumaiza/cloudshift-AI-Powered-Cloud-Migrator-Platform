"""Unit tests for score_service_candidates() — hybrid SAW + ontology scoring.

Tests cover:
  - PHASE 1 hard gates (equivalence, region, maturity, budget, preview+production)
  - PHASE 2 SAW formula (budget gradient, region_fit, complexity inversion)
  - Hybrid scoring: 0.70*SAW + 0.20*ontology + 0.10*schema_sim
  - SAW-only fallback when ontology is unavailable
  - Output structure (score_breakdown, rejected fields)
"""
import json
from unittest.mock import patch

import pytest

from agents.migration_planner.scoring_decision_tools import score_service_candidates


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cand(
    service="azurerm_storage_account",
    src_svc="aws_s3_bucket",
    eq=0.85,
    mat=0.90,
    cost=100.0,
    region=True,
    status="ga",
    complexity=0.30,
    src_args=None,
    tgt_args=None,
):
    c = {
        "service": service,
        "source_service": src_svc,
        "equivalence": eq,
        "maturity": mat,
        "monthly_cost": cost,
        "region_available": region,
        "maturity_status": status,
        "complexity": complexity,
    }
    if src_args is not None:
        c["source_required_args"] = src_args
    if tgt_args is not None:
        c["target_required_args"] = tgt_args
    return c


def _run(candidates, *, budget=500.0, production=True, ontology_score=None,
         src_provider="aws", tgt_provider="azure"):
    """Run score_service_candidates with an optional ontology mock."""
    with patch("rag.cloud_ontology.get_ontology_score", return_value=ontology_score):
        raw = score_service_candidates.invoke({
            "candidates":  json.dumps(candidates if isinstance(candidates, list) else [candidates]),
            "constraints": json.dumps({
                "budget": budget,
                "production": production,
                "source_provider": src_provider,
                "target_provider": tgt_provider,
            }),
        })
    return json.loads(raw)


# ── PHASE 1: Hard gates ───────────────────────────────────────────────────────

def test_gate_rejects_equivalence_below_050():
    results = _run(_cand(eq=0.40))
    assert results[0]["rejected"] is True
    assert "equivalence" in results[0]["reject_reason"]


def test_gate_rejects_unavailable_region_false():
    results = _run(_cand(region=False))
    assert results[0]["rejected"] is True


def test_gate_rejects_unavailable_region_string():
    results = _run(_cand(region="unavailable"))
    assert results[0]["rejected"] is True


def test_gate_rejects_deprecated_service():
    results = _run(_cand(status="deprecated"))
    assert results[0]["rejected"] is True


def test_gate_rejects_preview_in_production():
    results = _run(_cand(status="preview"), production=True)
    assert results[0]["rejected"] is True


def test_gate_allows_preview_outside_production():
    results = _run(_cand(status="preview"), production=False)
    assert results[0]["rejected"] is False


def test_gate_rejects_beta_in_production():
    results = _run(_cand(status="beta"), production=True)
    assert results[0]["rejected"] is True


def test_gate_rejects_cost_over_150pct_budget():
    results = _run(_cand(cost=2000.0), budget=1000.0)
    assert results[0]["rejected"] is True


def test_gate_allows_cost_at_budget():
    results = _run(_cand(cost=500.0), budget=500.0)
    assert results[0]["rejected"] is False


def test_gate_allows_boundary_equivalence_050():
    results = _run(_cand(eq=0.50))
    assert results[0]["rejected"] is False


# ── PHASE 2: SAW scoring ──────────────────────────────────────────────────────

def test_saw_score_present_in_breakdown():
    results = _run(_cand())
    assert "saw_score" in results[0]["score_breakdown"]


def test_score_breakdown_has_all_contributions():
    results = _run(_cand())
    bd = results[0]["score_breakdown"]
    for key in ("equivalence_contrib", "maturity_contrib", "budget_fit_contrib",
                "region_fit_contrib", "complexity_contrib"):
        assert key in bd, f"Missing breakdown key: {key}"


def test_region_neighbor_gives_060_fit():
    results = _run(_cand(region="neighbor"))
    r = results[0]
    assert r["rejected"] is False
    assert r["score_breakdown"]["normalized_inputs"]["region_fit"] == pytest.approx(0.60)


def test_budget_gradient_applied_between_100_and_150pct():
    """cost = 1200, budget = 1000 → gradient (not rejected), budget_fit < 1.0."""
    results = _run(_cand(cost=1200.0), budget=1000.0)
    r = results[0]
    assert r["rejected"] is False
    bf = r["score_breakdown"]["normalized_inputs"]["budget_fit"]
    assert 0.0 < bf < 1.0


def test_no_budget_constraint_gives_full_budget_fit():
    results = _run(_cand(cost=99999.0), budget=0)
    r = results[0]
    assert r["rejected"] is False
    assert r["score_breakdown"]["normalized_inputs"]["budget_fit"] == 1.0


def test_complexity_is_inverted():
    """complexity=0.3 → inverted=0.7 in breakdown."""
    results = _run(_cand(complexity=0.30))
    inv = results[0]["score_breakdown"]["normalized_inputs"]["complexity_inverted"]
    assert inv == pytest.approx(0.70)


# ── Hybrid scoring ────────────────────────────────────────────────────────────

def test_hybrid_mode_when_ontology_available():
    """final = 0.70*SAW + 0.20*onto + 0.10*schema_sim."""
    results = _run(_cand(), ontology_score=0.91)
    r = results[0]
    bd = r["score_breakdown"]
    saw   = bd["saw_score"]
    onto  = bd["ontology_score"]
    schema = bd["schema_similarity"]
    expected = round(min(0.70 * saw + 0.20 * onto + 0.10 * schema, 1.0), 4)
    assert abs(r["score"] - expected) < 1e-4


def test_hybrid_mode_label_in_breakdown():
    results = _run(_cand(), ontology_score=0.88)
    bd = results[0]["score_breakdown"]
    assert bd["ontology_score"] is not None
    assert "hybrid_formula" in bd


def test_saw_only_mode_when_ontology_none():
    results = _run(_cand(), ontology_score=None)
    r = results[0]
    assert r["score_breakdown"]["ontology_score"] is None
    # score must equal saw_score
    assert r["score"] == pytest.approx(r["score_breakdown"]["saw_score"])


def test_schema_similarity_computed_from_args():
    """shared args = {name, location} / max(3, 4) = 2/4 = 0.5."""
    c = _cand(
        src_args=["name", "location", "tier"],
        tgt_args=["name", "location", "sku", "resource_group"],
    )
    results = _run(c, ontology_score=None)
    schema_sim = results[0]["score_breakdown"]["schema_similarity"]
    assert schema_sim == pytest.approx(2 / 4)


def test_schema_similarity_defaults_to_05_when_args_unknown():
    results = _run(_cand(), ontology_score=None)
    assert results[0]["score_breakdown"]["schema_similarity"] == pytest.approx(0.5)


# ── Sorting and output ─────────────────────────────────────────────────────────

def test_results_sorted_by_score_descending():
    candidates = [_cand(eq=0.60, service="low"), _cand(eq=0.92, service="high")]
    results = _run(candidates, ontology_score=None)
    non_rejected = [r for r in results if not r["rejected"]]
    if len(non_rejected) >= 2:
        assert non_rejected[0]["score"] >= non_rejected[1]["score"]


def test_rejected_candidate_has_zero_score():
    results = _run(_cand(eq=0.30))
    assert results[0]["score"] == 0.0


def test_non_rejected_candidate_has_positive_score():
    results = _run(_cand(eq=0.85, mat=0.90))
    assert results[0]["score"] > 0.0


def test_score_capped_at_one():
    results = _run(_cand(eq=1.0, mat=1.0, cost=0, complexity=0.0), ontology_score=1.0)
    assert results[0]["score"] <= 1.0


# ── Input validation ──────────────────────────────────────────────────────────

def test_invalid_json_candidates_returns_error():
    raw = score_service_candidates.invoke({"candidates": "not_valid_json", "constraints": "{}"})
    parsed = json.loads(raw)
    assert "error" in parsed


def test_non_list_candidates_returns_error():
    raw = score_service_candidates.invoke({
        "candidates": json.dumps({"not": "a list"}),
        "constraints": "{}",
    })
    parsed = json.loads(raw)
    assert "error" in parsed
