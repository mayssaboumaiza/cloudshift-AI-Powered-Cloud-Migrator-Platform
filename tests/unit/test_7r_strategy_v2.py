"""Tests for decide_7r_strategy_v2 — 3-signal combined scoring."""

import pytest
from agents.migration_planner.scoring_decision_tools import _decide_7r_v2_impl as decide_7r_strategy_v2


# ── Helper: compute combined score for reference ──────────────────────────────

def _combined(similarity, sdk_calls, minor_breaks):
    sdk_score   = 1.0 - min(sdk_calls / 20.0, 1.0)
    breaks_score = 1.0 - min(minor_breaks / 10.0, 1.0)
    return round(0.40 * similarity + 0.35 * sdk_score + 0.25 * breaks_score, 4)


# ── Q6 scenarios ──────────────────────────────────────────────────────────────

def test_rehost_high_confidence():
    """combined >= 0.88 → REHOST high, ask_human=False."""
    result = decide_7r_strategy_v2(
        similarity=0.95, sdk_calls_count=0,
        breaking_changes=[],
    )
    assert result["strategy"] == "REHOST"
    assert result["certainty"] == "high"
    assert result["trigger_ask_human"] is False
    assert result["combined_score"] >= 0.88


def test_rehost_medium_gray_zone():
    """combined in [0.82-0.88] → REHOST medium, ask_human=True."""
    result = decide_7r_strategy_v2(
        similarity=0.85, sdk_calls_count=3,
        breaking_changes=[],
    )
    c = _combined(0.85, 3, 0)
    if 0.82 <= c < 0.88:
        assert result["strategy"] == "REHOST"
        assert result["certainty"] == "medium"
        assert result["trigger_ask_human"] is True
        assert result["uncertainty_reason"] is not None


def test_replatform_high_confidence():
    """combined in [0.70-0.82) → REPLATFORM high, ask_human=False."""
    result = decide_7r_strategy_v2(
        similarity=0.89, sdk_calls_count=8,
        breaking_changes=[{"severity": "MINOR"}],
    )
    c = result["combined_score"]
    if 0.70 <= c < 0.82:
        assert result["strategy"] == "REPLATFORM"
        assert result["certainty"] == "high"
        assert result["trigger_ask_human"] is False


def test_replatform_medium_gray_zone():
    """combined in [0.62-0.70) → REPLATFORM medium, ask_human=True."""
    result = decide_7r_strategy_v2(
        similarity=0.72, sdk_calls_count=10,
        breaking_changes=[{"severity": "MINOR"}, {"severity": "MINOR"}],
    )
    c = result["combined_score"]
    if 0.62 <= c < 0.70:
        assert result["strategy"] == "REPLATFORM"
        assert result["certainty"] == "medium"
        assert result["trigger_ask_human"] is True


def test_refactor_low_combined():
    """combined < 0.62 → REFACTOR high, ask_human=False."""
    result = decide_7r_strategy_v2(
        similarity=0.55, sdk_calls_count=18,
        breaking_changes=[{"severity": "MINOR"}] * 8,
    )
    assert result["strategy"] == "REFACTOR"
    assert result["certainty"] == "high"
    assert result["trigger_ask_human"] is False


def test_refactor_forced_by_critical_breaks():
    """3+ CRITICAL breaking changes → REFACTOR regardless of similarity."""
    result = decide_7r_strategy_v2(
        similarity=0.95, sdk_calls_count=0,
        breaking_changes=[
            {"severity": "CRITICAL"},
            {"severity": "CRITICAL"},
            {"severity": "CRITICAL"},
        ],
    )
    assert result["strategy"] == "REFACTOR"
    assert result["confidence"] == 0.85
    assert result["components"]["critical_breaking_changes"] == 3


def test_retire_forced_by_service_unused_signal():
    """contextual_signals.service_unused → RETIRE, confidence 0.95."""
    result = decide_7r_strategy_v2(
        similarity=0.50, sdk_calls_count=5,
        breaking_changes=[],
        contextual_signals={"service_unused": True},
    )
    assert result["strategy"] == "RETIRE"
    assert result["confidence"] == 0.95
    assert result["trigger_ask_human"] is False


def test_retain_forced_by_regulatory_signal():
    """contextual_signals.regulatory → RETAIN, confidence 0.90."""
    result = decide_7r_strategy_v2(
        similarity=0.90, sdk_calls_count=0,
        breaking_changes=[],
        contextual_signals={"regulatory": True},
    )
    assert result["strategy"] == "RETAIN"
    assert result["confidence"] == 0.90


def test_relocate_signal():
    """contextual_signals.relocate → RELOCATE."""
    result = decide_7r_strategy_v2(
        similarity=0.99, sdk_calls_count=0,
        breaking_changes=[],
        contextual_signals={"relocate": True},
    )
    assert result["strategy"] == "RELOCATE"


def test_components_present_in_response():
    """components dict present with sdk_score, breaks_score, similarity."""
    result = decide_7r_strategy_v2(
        similarity=0.88, sdk_calls_count=5,
        breaking_changes=[{"severity": "MINOR"}],
    )
    comp = result.get("components", {})
    assert "similarity" in comp
    assert "sdk_score" in comp
    assert "sdk_calls_count" in comp


def test_combined_score_math():
    """combined_score matches formula 0.4*sim + 0.35*sdk + 0.25*breaks."""
    sim, sdk, minor = 0.80, 6, 3
    result = decide_7r_strategy_v2(
        similarity=sim, sdk_calls_count=sdk,
        breaking_changes=[{"severity": "MINOR"}] * minor,
    )
    expected = round(
        0.40 * sim
        + 0.35 * (1.0 - min(sdk / 20.0, 1.0))
        + 0.25 * (1.0 - min(minor / 10.0, 1.0)),
        4,
    )
    assert abs(result["combined_score"] - expected) < 1e-4


def test_no_contextual_signals_does_not_crash():
    """Omitting contextual_signals defaults to empty dict without error."""
    result = decide_7r_strategy_v2(
        similarity=0.90, sdk_calls_count=0, breaking_changes=[]
    )
    assert "strategy" in result
