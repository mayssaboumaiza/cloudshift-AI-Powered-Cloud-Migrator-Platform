"""Unit tests for dynamic cost functions in service_lookup_tools.py.

Tests cover:
  - _instance_class_vcores()            : AWS instance class → vCore count (algorithmic)
  - _azure_postgresql_cost_by_vcores()  : Azure API call with vCore filter
  - _llm_estimate_cost()                : GPT-4o fallback when APIs unavailable
  - estimate_cost() end-to-end          : RDS instance class → Azure API → result
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.migration_planner.service_lookup_tools import (
    _instance_class_vcores,
    _azure_postgresql_cost_by_vcores,
    _llm_estimate_cost,
    _PRICING_CACHE,
)


# ── _instance_class_vcores ────────────────────────────────────────────────────

@pytest.mark.parametrize("instance_class,expected_vcores", [
    ("db.t3.micro",    1),
    ("db.t3.small",    1),
    ("db.t3.medium",   2),
    ("db.t3.large",    2),
    ("db.t3.xlarge",   4),
    ("db.t3.2xlarge",  8),
    ("db.m5.4xlarge", 16),
    ("db.r6g.large",   2),
    ("db.r6g.xlarge",  4),
    ("db.r5.2xlarge",  8),
])
def test_instance_class_vcores(instance_class, expected_vcores):
    assert _instance_class_vcores(instance_class) == expected_vcores


def test_instance_class_vcores_unknown_defaults_to_2():
    assert _instance_class_vcores("db.unknown.custom") == 2


def test_instance_class_vcores_case_insensitive():
    assert _instance_class_vcores("DB.T3.XLARGE") == 4


# ── _azure_postgresql_cost_by_vcores ─────────────────────────────────────────

def _azure_api_response(price_per_hour: float) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"Items": [{"retailPrice": price_per_hour}]}
    return resp


def test_azure_postgresql_cost_by_vcores_real_price():
    """API returns 0.095 $/h → 0.095 * 730 * 0.92 ≈ 63.8 EUR/month."""
    with patch("agents.migration_planner.service_lookup_tools.httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.get.return_value = \
            _azure_api_response(0.095)
        result = _azure_postgresql_cost_by_vcores(2, "westeurope")
    assert result is not None
    assert result == pytest.approx(0.095 * 730 * 0.92, rel=1e-3)


def test_azure_postgresql_cost_by_vcores_no_items():
    """Empty Items array → returns None."""
    with patch("agents.migration_planner.service_lookup_tools.httpx") as mock_httpx:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"Items": []}
        mock_httpx.Client.return_value.__enter__.return_value.get.return_value = resp
        result = _azure_postgresql_cost_by_vcores(2, "westeurope")
    # Second attempt (General Purpose fallback) also returns empty
    assert result is None


def test_azure_postgresql_cost_api_error_returns_none():
    """API request raises exception → returns None."""
    with patch("agents.migration_planner.service_lookup_tools.httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.get.side_effect = \
            ConnectionError("network down")
        result = _azure_postgresql_cost_by_vcores(4, "westeurope")
    assert result is None


def test_azure_postgresql_cost_unknown_region_defaults_westeurope():
    """Non-standard region string falls back to westeurope."""
    with patch("agents.migration_planner.service_lookup_tools.httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.get.return_value = \
            _azure_api_response(0.10)
        result = _azure_postgresql_cost_by_vcores(2, "eu-west-1-unknown")
    assert result is not None  # returns a number even with fallback region


# ── _llm_estimate_cost ────────────────────────────────────────────────────────

def test_llm_estimate_cost_returns_float():
    """LLM returns '45.0' → parsed as float."""
    mock_resp = MagicMock()
    mock_resp.content = "45.0"
    with patch("langchain_openai.AzureChatOpenAI") as mock_llm_cls:
        mock_llm_cls.return_value.invoke.return_value = mock_resp
        result = _llm_estimate_cost("cloud-storage", "azure", "westeurope")
    assert result == pytest.approx(45.0)


def test_llm_estimate_cost_handles_euro_symbol():
    """LLM returns '€38' — symbol stripped before parse."""
    mock_resp = MagicMock()
    mock_resp.content = "€38"
    with patch("langchain_openai.AzureChatOpenAI") as mock_llm_cls:
        mock_llm_cls.return_value.invoke.return_value = mock_resp
        result = _llm_estimate_cost("redis", "azure", "westeurope")
    assert result == pytest.approx(38.0)


def test_llm_estimate_cost_invoke_failure_returns_none():
    """If LLM invocation raises → returns None gracefully."""
    with patch("langchain_openai.AzureChatOpenAI") as mock_llm_cls:
        mock_llm_cls.return_value.invoke.side_effect = Exception("LLM timeout")
        result = _llm_estimate_cost("postgresql", "azure", "westeurope")
    assert result is None


def test_llm_estimate_cost_non_numeric_returns_none():
    """LLM returns 'unknown' (non-numeric) → returns None."""
    mock_resp = MagicMock()
    mock_resp.content = "I cannot estimate this."
    with patch("langchain_openai.AzureChatOpenAI") as mock_llm_cls:
        mock_llm_cls.return_value.invoke.return_value = mock_resp
        result = _llm_estimate_cost("weird-service", "azure", "westeurope")
    assert result is None


# ── estimate_cost end-to-end for RDS → PostgreSQL ────────────────────────────

def test_estimate_cost_rds_instance_uses_azure_api():
    """instance_class + postgresql → _azure_postgresql_cost_by_vcores called."""
    _PRICING_CACHE.clear()

    with patch(
        "agents.migration_planner.service_lookup_tools._azure_postgresql_cost_by_vcores",
        return_value=56.0,
    ) as mock_api:
        from agents.migration_planner.service_lookup_tools import estimate_cost
        result_json = estimate_cost.invoke({
            "service": "postgresql-flexible-server",
            "region": "westeurope",
            "cloud_target": "azure",
            "instance_class": "db.t3.medium",
        })
    import json
    result = json.loads(result_json)
    mock_api.assert_called_once_with(2, "westeurope")  # db.t3.medium = 2 vCores
    assert result["monthly_cost_eur"] == 56.0
    assert result["confidence"] == "real"
    assert "azure-retail-prices-api" in result["source"]


def test_estimate_cost_no_instance_class_uses_service_api():
    """Without instance_class, falls through to standard Azure API path."""
    _PRICING_CACHE.clear()

    with patch("agents.migration_planner.service_lookup_tools.httpx") as mock_httpx:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"Items": [{"retailPrice": 0.060, "unitOfMeasure": "1 Hour"}]}
        mock_httpx.Client.return_value.__enter__.return_value.get.return_value = resp
        from agents.migration_planner.service_lookup_tools import estimate_cost
        result_json = estimate_cost.invoke({
            "service": "container-instances",
            "region": "westeurope",
            "cloud_target": "azure",
        })
    import json
    result = json.loads(result_json)
    assert result["monthly_cost_eur"] == pytest.approx(0.060 * 730 * 0.92, rel=1e-2)
    assert result["source"] == "azure-retail-prices-api"
