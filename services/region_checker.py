"""
region_checker.py — Region availability with PostgreSQL cache + live APIs + static fallback.

Flow:
  1. Check PostgreSQL cache (TTL 24h live, 7d static)
  2. Call live cloud pricing API for the provider
  3. Store result in cache
  4. If API unavailable → static fallback table

calculate_region_fit() converts availability to a 0-1 float:
  available + ga      → 1.0
  available + preview → 0.8
  nearest region      → 0.6
  unavailable         → 0.0
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("RegionChecker")

CACHE_TTL_LIVE   = timedelta(hours=24)
CACHE_TTL_STATIC = timedelta(days=7)

# Static fallback: (provider, region) → availability
_STATIC_FALLBACK: dict[str, dict[str, bool]] = {
    "azure": {
        "westeurope": True, "northeurope": True, "eastus": True,
        "eastus2": True, "westus2": True, "francecentral": True,
        "germanywestcentral": True, "uksouth": True,
    },
    "aws": {
        "eu-west-1": True, "eu-west-3": True, "eu-central-1": True,
        "us-east-1": True, "us-east-2": True, "us-west-2": True,
        "ap-southeast-1": True,
    },
    "gcp": {
        "europe-west1": True, "europe-west4": True, "us-central1": True,
        "us-east1": True, "us-west1": True, "asia-southeast1": True,
    },
}

# Nearest-region map: if target region unavailable, suggest closest GA region
_NEAREST_REGION: dict[str, dict[str, str]] = {
    "azure": {
        "switzerlandnorth": "germanywestcentral",
        "norwayeast":       "northeurope",
        "polandcentral":    "germanywestcentral",
    },
    "aws": {
        "eu-north-1":   "eu-west-1",
        "eu-south-1":   "eu-central-1",
        "me-south-1":   "eu-west-1",
    },
    "gcp": {
        "europe-north1": "europe-west4",
        "europe-west8":  "europe-west1",
        "me-west1":      "europe-west1",
    },
}


def check_region_availability(
    service: str,
    region: str,
    provider: str,
    db: Any = None,
) -> dict:
    """Check whether a cloud service is available in a given region.

    Args:
        service:  Terraform resource type or canonical service name
        region:   Cloud region identifier (e.g. "westeurope", "eu-west-1")
        provider: "azure" | "aws" | "gcp"
        db:       SQLAlchemy Session (optional — skipped if None)

    Returns:
        {"available": bool, "type": "ga"|"preview"|"unknown",
         "source": str, "from_cache": bool}
    """
    provider = provider.lower().strip()
    region   = region.lower().strip()

    # ── Step 1: PostgreSQL cache ─────────────────────────────────────────────
    if db is not None:
        try:
            row = db.execute(
                """
                SELECT available, availability_type, source, expires_at
                FROM region_availability_cache
                WHERE service_name = :s AND region = :r AND provider = :p
                """,
                {"s": service, "r": region, "p": provider},
            ).fetchone()
            if row and row.expires_at > datetime.now():
                return {
                    "available":  bool(row.available),
                    "type":       row.availability_type or "ga",
                    "source":     row.source,
                    "from_cache": True,
                }
        except Exception as exc:
            logger.warning("Cache lookup failed: %s", exc)

    # ── Step 2: Live API ─────────────────────────────────────────────────────
    result: dict | None = None
    if provider == "azure":
        result = _check_azure_live(service, region)
    elif provider == "aws":
        result = _check_aws_live(service, region)
    elif provider == "gcp":
        result = _check_gcp_live(service, region)

    if result:
        _store_cache(db, service, region, provider, result, CACHE_TTL_LIVE)
        return result

    # ── Step 3: Static fallback ──────────────────────────────────────────────
    logger.warning("Region check fallback for %s in %s (%s)", service, region, provider)
    fallback = _static_fallback(region, provider)
    _store_cache(db, service, region, provider, fallback, CACHE_TTL_STATIC)
    return fallback


def _check_azure_live(service: str, region: str) -> dict | None:
    try:
        import requests
        url = (
            "https://prices.azure.com/api/retail/prices"
            f"?$filter=serviceName eq '{service}' and armRegionName eq '{region}'"
        )
        r = requests.get(url, timeout=5)
        items = r.json().get("Items", [])
        return {
            "available":  len(items) > 0,
            "type":       "ga",
            "source":     "azure_retail_prices_api_live",
            "from_cache": False,
        }
    except Exception as exc:
        logger.error("Azure region check failed: %s", exc)
        return None


def _check_aws_live(service: str, region: str) -> dict | None:
    try:
        import boto3
        client = boto3.client("pricing", region_name="us-east-1")
        response = client.get_products(
            ServiceCode=service,
            Filters=[{"Type": "TERM_MATCH", "Field": "location", "Value": region}],
        )
        return {
            "available":  len(response.get("PriceList", [])) > 0,
            "type":       "ga",
            "source":     "aws_pricing_api_live",
            "from_cache": False,
        }
    except Exception as exc:
        logger.error("AWS region check failed: %s", exc)
        return None


def _check_gcp_live(service: str, region: str) -> dict | None:
    try:
        import requests
        url = "https://cloudpricingcalculator.appspot.com/static/data/pricelist.json"
        data    = requests.get(url, timeout=5).json()
        key     = f"{service}-{region}"
        present = key in data.get("gcp_price_list", {})
        return {
            "available":  present,
            "type":       "ga",
            "source":     "gcp_pricelist_json_live",
            "from_cache": False,
        }
    except Exception as exc:
        logger.error("GCP region check failed: %s", exc)
        return None


def _static_fallback(region: str, provider: str) -> dict:
    available = _STATIC_FALLBACK.get(provider, {}).get(region, False)
    return {
        "available":  available,
        "type":       "ga" if available else "unknown",
        "source":     "static_fallback_table",
        "from_cache": False,
    }


def _store_cache(db, service, region, provider, result, ttl):
    if db is None:
        return
    try:
        now = datetime.now()
        db.execute(
            """
            INSERT INTO region_availability_cache
                (service_name, region, provider, available,
                 availability_type, source, checked_at, expires_at)
            VALUES (:s, :r, :p, :a, :t, :src, :c, :e)
            ON CONFLICT (service_name, region, provider)
            DO UPDATE SET
                available         = EXCLUDED.available,
                availability_type = EXCLUDED.availability_type,
                source            = EXCLUDED.source,
                checked_at        = EXCLUDED.checked_at,
                expires_at        = EXCLUDED.expires_at
            """,
            {
                "s":   service,
                "r":   region,
                "p":   provider,
                "a":   result["available"],
                "t":   result.get("type", "ga"),
                "src": result.get("source", "unknown"),
                "c":   now,
                "e":   now + ttl,
            },
        )
        db.commit()
    except Exception as exc:
        logger.warning("Cache store failed: %s", exc)


def calculate_region_fit(
    service: str,
    target_region: str,
    provider: str,
    db: Any = None,
) -> dict:
    """Convert region availability into a 0-1 fit score for SAW scoring.

    Returns:
        {"region_fit": float, "label": str, "source": str}
    """
    result = check_region_availability(service, target_region, provider, db)

    if result["available"]:
        fit   = 0.8 if result.get("type") == "preview" else 1.0
        label = result.get("type", "available")
        return {"region_fit": fit, "label": label, "source": result["source"]}

    neighbor = _NEAREST_REGION.get(provider, {}).get(target_region)
    if neighbor:
        return {
            "region_fit": 0.6,
            "label":      f"nearest: {neighbor}",
            "source":     result["source"],
        }

    return {"region_fit": 0.0, "label": "unavailable", "source": result["source"]}
