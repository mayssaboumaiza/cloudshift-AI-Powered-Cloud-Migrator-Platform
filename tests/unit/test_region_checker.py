"""Tests for services/region_checker.py — cache, live APIs, fallback, region_fit."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.region_checker import (
    check_region_availability,
    calculate_region_fit,
    _static_fallback,
    _NEAREST_REGION,
    CACHE_TTL_LIVE,
    CACHE_TTL_STATIC,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_db_with_cache(available=True, expires_delta=timedelta(hours=1)):
    """Return a mock db that returns a valid cache row."""
    db = MagicMock()
    row = MagicMock()
    row.available = available
    row.availability_type = "ga"
    row.source = "test_cache"
    row.expires_at = datetime.now() + expires_delta
    db.execute.return_value.fetchone.return_value = row
    return db


def _mock_db_no_cache():
    """Return a mock db with no cached row."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None
    return db


# ── Cache behavior ────────────────────────────────────────────────────────────

def test_cache_hit_returns_without_api_call():
    """Valid cache entry should be returned without hitting live API."""
    db = _mock_db_with_cache(available=True)
    with patch("services.region_checker._check_azure_live") as mock_live:
        result = check_region_availability("Virtual Machines", "westeurope", "azure", db)
    mock_live.assert_not_called()
    assert result["available"] is True
    assert result["from_cache"] is True
    assert result["source"] == "test_cache"


def test_expired_cache_triggers_api_call():
    """Expired cache should trigger live API call."""
    db = _mock_db_with_cache(available=True, expires_delta=timedelta(hours=-1))
    live_result = {"available": True, "type": "ga",
                   "source": "azure_retail_prices_api_live", "from_cache": False}
    with patch("services.region_checker._check_azure_live", return_value=live_result) as mock_live:
        result = check_region_availability("Virtual Machines", "westeurope", "azure", db)
    mock_live.assert_called_once()
    assert result["from_cache"] is False


def test_cache_miss_triggers_api_call():
    """No cache row → live API must be called."""
    db = _mock_db_no_cache()
    live_result = {"available": True, "type": "ga",
                   "source": "azure_retail_prices_api_live", "from_cache": False}
    with patch("services.region_checker._check_azure_live", return_value=live_result):
        result = check_region_availability("Virtual Machines", "westeurope", "azure", db)
    assert result["from_cache"] is False
    assert result["available"] is True


# ── API failure → static fallback ─────────────────────────────────────────────

def test_fallback_when_api_unavailable():
    """If live API returns None, static fallback is used."""
    db = _mock_db_no_cache()
    with patch("services.region_checker._check_azure_live", return_value=None):
        result = check_region_availability("Virtual Machines", "westeurope", "azure", db)
    assert result["source"] == "static_fallback_table"


def test_static_fallback_known_azure_region():
    """westeurope is in static fallback → available True."""
    result = _static_fallback("westeurope", "azure")
    assert result["available"] is True
    assert result["source"] == "static_fallback_table"


def test_static_fallback_unknown_region():
    """Unknown region → available False."""
    result = _static_fallback("mars-north-1", "azure")
    assert result["available"] is False


# ── region_fit score ──────────────────────────────────────────────────────────

def test_region_fit_1_when_available_ga():
    """available + ga → region_fit = 1.0."""
    with patch("services.region_checker.check_region_availability",
               return_value={"available": True, "type": "ga",
                              "source": "test", "from_cache": False}):
        result = calculate_region_fit("storage", "westeurope", "azure")
    assert result["region_fit"] == 1.0
    assert result["label"] == "ga"


def test_region_fit_08_when_preview():
    """available + preview → region_fit = 0.8."""
    with patch("services.region_checker.check_region_availability",
               return_value={"available": True, "type": "preview",
                              "source": "test", "from_cache": False}):
        result = calculate_region_fit("storage", "westeurope", "azure")
    assert result["region_fit"] == 0.8
    assert result["label"] == "preview"


def test_region_fit_06_when_nearest_region():
    """Unavailable region with known nearest → region_fit = 0.6."""
    provider = "azure"
    region   = list(_NEAREST_REGION.get(provider, {}).keys())
    if not region:
        pytest.skip("No nearest region configured for azure")
    target = region[0]
    with patch("services.region_checker.check_region_availability",
               return_value={"available": False, "type": "unknown",
                              "source": "static", "from_cache": False}):
        result = calculate_region_fit("storage", target, provider)
    assert result["region_fit"] == 0.6
    assert "nearest" in result["label"]


def test_region_fit_0_when_unavailable_no_neighbor():
    """Unavailable + no nearest region → region_fit = 0.0."""
    with patch("services.region_checker.check_region_availability",
               return_value={"available": False, "type": "unknown",
                              "source": "static", "from_cache": False}):
        result = calculate_region_fit("storage", "mars-north-1", "azure")
    assert result["region_fit"] == 0.0
    assert result["label"] == "unavailable"
