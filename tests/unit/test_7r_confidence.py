"""
test_7r_confidence.py — Unit tests for decide_7r_strategy() confidence zones.

Tests verify the 5 confidence zones (using a neutral infra pair — aws_instance →
azurerm_linux_virtual_machine — which has no anti-boost guard and no SDK-change
requirement, so the raw equivalence score drives the zone logic directly):

  eq >= 0.97            → REHOST,      confidence=0.95, ask_human=False
  0.94 <= eq < 0.97     → REHOST,      confidence=0.75, ask_human=True
  0.86 <= eq < 0.94     → REPLATFORM,  confidence=0.90, ask_human=False
  0.82 <= eq < 0.86     → REPLATFORM,  confidence=0.70, ask_human=True
  eq < 0.82             → REFACTOR,    confidence=0.80, ask_human=False

Also tests:
  - contextual signals (RETAIN, RETIRE, REPURCHASE)
  - anti-boost guard: database pairs capped at REPLATFORM even at high similarity
"""

import json
import pytest
from agents.migration_planner.scoring_decision_tools import decide_7r_strategy


def _call(
    eq: float,
    signals: list[str] | None = None,
    has_critical: bool = False,
    source: str = "aws_instance",
    target: str = "azurerm_linux_virtual_machine",
) -> dict:
    """Helper — neutral compute pair by default (no anti-boost, no SDK-change guard)."""
    if signals:
        bc = json.dumps({"signals": signals, "items": []})
    elif has_critical:
        bc = json.dumps([{"severity": "CRITICAL", "pattern": "x", "replacement": "y"}])
    else:
        bc = json.dumps([])
    result = decide_7r_strategy.invoke({
        "source_service":    source,
        "target_candidate":  target,
        "equivalence_score": eq,
        "breaking_changes":  bc,
    })
    return json.loads(result)


# ── Zone tests (neutral compute pair — no guard interference) ─────────────────

def test_rehost_certain():
    """eq=0.97 → REHOST certain zone (compute pair, no SDK-change guard)."""
    r = _call(0.97)
    assert r["strategy"]          == "REHOST"
    assert r["confidence"]        == 0.95
    assert r["certainty"]         == "high"
    assert r["trigger_ask_human"] is False


def test_rehost_gray_zone():
    """eq=0.95 → REHOST gray zone (compute pair)."""
    r = _call(0.95)
    assert r["strategy"]          == "REHOST"
    assert r["confidence"]        == 0.75
    assert r["certainty"]         == "medium"
    assert r["trigger_ask_human"] is True
    assert "uncertainty_reason" in r


def test_replatform_certain():
    """eq=0.88 → REPLATFORM certain zone (compute pair)."""
    r = _call(0.88)
    assert r["strategy"]          == "REPLATFORM"
    assert r["confidence"]        == 0.90
    assert r["certainty"]         == "high"
    assert r["trigger_ask_human"] is False


def test_replatform_gray_zone():
    """eq=0.83 → REPLATFORM gray zone (compute pair)."""
    r = _call(0.83)
    assert r["strategy"]          == "REPLATFORM"
    assert r["confidence"]        == 0.70
    assert r["certainty"]         == "medium"
    assert r["trigger_ask_human"] is True
    assert "uncertainty_reason" in r


def test_refactor_low_equivalence():
    """eq=0.65 → REFACTOR (compute pair)."""
    r = _call(0.65)
    assert r["strategy"]          == "REFACTOR"
    assert r["confidence"]        == 0.80
    assert r["certainty"]         == "high"
    assert r["trigger_ask_human"] is False


def test_refactor_critical_breaking_change():
    """CRITICAL breaking change forces REFACTOR regardless of equivalence score."""
    r = _call(0.95, has_critical=True)
    assert r["strategy"] == "REFACTOR"


def test_retain_signal():
    """'regulatory' signal → RETAIN regardless of equivalence."""
    r = _call(0.98, signals=["regulatory"])
    assert r["strategy"]   == "RETAIN"
    assert r["confidence"] == 0.90


def test_retire_signal():
    """'service_unused' signal → RETIRE regardless of equivalence."""
    r = _call(0.99, signals=["service_unused"])
    assert r["strategy"]   == "RETIRE"
    assert r["confidence"] == 0.95


def test_repurchase_signal():
    """'saas_alt' signal → REPURCHASE."""
    r = _call(0.85, signals=["saas_alt"])
    assert r["strategy"] == "REPURCHASE"


def test_thresholds_present_in_response():
    """Every response must include thresholds_used for traceability."""
    r = _call(0.88)
    assert "thresholds_used" in r
    assert "rehost_certain" in r["thresholds_used"]


def test_exact_boundary_rehost_high():
    """eq exactly at REHOST_HIGH (0.97) falls in the certain zone (compute pair)."""
    r = _call(0.97)
    assert r["strategy"]  == "REHOST"
    assert r["certainty"] == "high"


def test_exact_boundary_replatform_low():
    """eq exactly at REPLATFORM_LOW (0.82) falls in the gray zone (compute pair)."""
    r = _call(0.82)
    assert r["strategy"]  == "REPLATFORM"
    assert r["certainty"] == "medium"


# ── Anti-boost guard tests ────────────────────────────────────────────────────

def test_anti_boost_database_pair_high_similarity():
    """Database pair at inflated similarity 0.97 → demoted to REPLATFORM (anti-boost guard)."""
    r = _call(0.97, source="aws_rds_cluster", target="azurerm_postgresql_flexible_server")
    assert r["strategy"] == "REPLATFORM"
    assert r.get("anti_boost_applied") is True


def test_anti_boost_database_pair_gray_zone():
    """Database pair at gray-zone similarity 0.95 → still REPLATFORM, not REHOST."""
    r = _call(0.95, source="aws_rds_cluster", target="azurerm_postgresql_flexible_server")
    assert r["strategy"] == "REPLATFORM"
    assert r.get("anti_boost_applied") is True


def test_anti_boost_storage_pair():
    """S3 → Azure Storage at inflated similarity → REPLATFORM (SDK change required)."""
    r = _call(0.97, source="aws_s3_bucket", target="azurerm_storage_account")
    assert r["strategy"] == "REPLATFORM"
    assert r.get("anti_boost_applied") is True


def test_anti_boost_not_applied_for_compute():
    """Compute pair at high similarity → REHOST (guard must NOT fire for compute)."""
    r = _call(0.97, source="aws_instance", target="azurerm_linux_virtual_machine")
    assert r["strategy"] == "REHOST"
    assert not r.get("anti_boost_applied")
