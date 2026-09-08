"""
service_lookup_tools.py — Agent 01 live cloud service lookup tools.

Responsibilities:
  - Look up service equivalents from service_registry.yaml.
  - Check region availability via public provider APIs (falls back to static lists).
  - Check budget fit via public pricing APIs (falls back to known baselines).
  - Assess Terraform resource maturity via GitHub commit history.
  - Estimate monthly cost in EUR via public pricing APIs + static baselines.
  - Check region availability with suggested alternatives.

All tools are:
  - Decorated with @tool (langchain_core.tools)
  - Return str (JSON-serialised)
  - Zero LLM calls — deterministic API calls + heuristics only

HTTP calls use httpx (optional); TTL caching via cachetools (optional).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── Optional HTTP + cache dependencies ───────────────────────────────────────
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False
    logger.warning("httpx not installed — HTTP gate tools will use fallback only")

try:
    from cachetools import TTLCache
    _HAS_CACHETOOLS = True
except ImportError:
    _HAS_CACHETOOLS = False
    logger.warning("cachetools not installed — TTL caching disabled")

# TTL caches: 3600s for region/maturity (GitHub rate: 60 req/h), 1800s for pricing
_REGION_CACHE:   Any = TTLCache(maxsize=256, ttl=3600) if _HAS_CACHETOOLS else {}
_MATURITY_CACHE: Any = TTLCache(maxsize=256, ttl=3600) if _HAS_CACHETOOLS else {}
_PRICING_CACHE:  Any = TTLCache(maxsize=128, ttl=1800) if _HAS_CACHETOOLS else {}

_HTTP_TIMEOUT = 10  # seconds
_GITHUB_HEADERS = {
    "User-Agent": "cloud-migrator-agent/1.0",
    "Accept": "application/vnd.github.v3+json",
}
if os.getenv("GITHUB_TOKEN"):
    _GITHUB_HEADERS["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"

# Official Terraform provider repos — scoped to resource doc paths to reduce noise
_GITHUB_REPOS = {
    "aws":   "hashicorp/terraform-provider-aws",
    "azure": "hashicorp/terraform-provider-azurerm",
    "gcp":   "hashicorp/terraform-provider-google",
}
_GITHUB_REPO_DOC_PATHS = {
    "aws":   "website/docs/r",
    "azure": "website/docs/r",
    "gcp":   "website/docs/r",
}

# ── Provider normalisation ────────────────────────────────────────────────────
_PROVIDER_ALIASES = {
    "aws": "aws", "amazon": "aws",
    "azure": "azure", "azurerm": "azure", "microsoft": "azure",
    "gcp": "gcp", "google": "gcp", "googlecloud": "gcp",
}
_PROVIDER_RESOURCE_PREFIXES = {
    "aws":   "aws_",
    "azure": "azurerm_",
    "gcp":   "google_",
}

# ── Pricing lookup tables ─────────────────────────────────────────────────────
_AWS_PRICING_CODES = {
    "s3": "AmazonS3", "ec2": "AmazonEC2", "rds": "AmazonRDS",
    "dynamodb": "AmazonDynamoDB", "lambda": "AWSLambda",
    "eks": "AmazonEKS", "ecs": "AmazonECS", "sqs": "AWSQueueService",
    "sns": "AmazonSNS", "cloudfront": "AmazonCloudFront",
}

_AZURE_PRICING_NAMES = {
    "blob-storage": "Azure Blob Storage",
    "blob": "Azure Blob Storage",
    "azure-blob-storage": "Azure Blob Storage",
    "storage-account": "Azure Blob Storage",
    "azure-storage": "Azure Blob Storage",
    "cosmos": "Azure Cosmos DB",
    "cosmosdb": "Azure Cosmos DB",
    "azure-cosmos-db": "Azure Cosmos DB",
    "azure-database-postgresql": "Azure Database for PostgreSQL",
    "postgresql": "Azure Database for PostgreSQL",
    "postgres": "Azure Database for PostgreSQL",
    "azure-postgresql": "Azure Database for PostgreSQL",
    "functions": "Azure Functions",
    "azure-functions": "Azure Functions",
    "container-apps": "Azure Container Apps",
    "azure-container-apps": "Azure Container Apps",
    "aks": "Azure Kubernetes Service",
    "azure-kubernetes-service": "Azure Kubernetes Service",
    "kubernetes": "Azure Kubernetes Service",
    "service-bus": "Service Bus",
    "azure-service-bus": "Service Bus",
    "event-hubs": "Event Hubs",
    "azure-event-hubs": "Event Hubs",
    "virtual-machine": "Virtual Machines",
    "virtual-machines": "Virtual Machines",
    "vm": "Virtual Machines",
    "azure-vm": "Virtual Machines",
    "azure-virtual-machine": "Virtual Machines",
    "linux-virtual-machine": "Virtual Machines",
    "azurerm-linux-virtual-machine": "Virtual Machines",
    "app-service": "Azure App Service",
    "azure-app-service": "Azure App Service",
    "web-app": "Azure App Service",
    "app-service-plan": "Azure App Service",
    "sql-database": "SQL Database",
    "azure-sql": "SQL Database",
    "azure-sql-database": "SQL Database",
    "sql-server": "SQL Database",
    "cache-for-redis": "Azure Cache for Redis",
    "azure-cache-for-redis": "Azure Cache for Redis",
    "redis": "Azure Cache for Redis",
    "redis-cache": "Azure Cache for Redis",
    "container-registry": "Container Registry",
    "azure-container-registry": "Container Registry",
    "acr": "Container Registry",
    "keyvault": "Key Vault",
    "key-vault": "Key Vault",
    "azure-key-vault": "Key Vault",
    "api-management": "API Management",
    "azure-api-management": "API Management",
    "apim": "API Management",
    "cognitive-search": "Azure Cognitive Search",
    "azure-cognitive-search": "Azure Cognitive Search",
    "azure-search": "Azure Cognitive Search",
    "search": "Azure Cognitive Search",
    "azure-openai": "Azure OpenAI",
    "openai": "Azure OpenAI",
    "azure-openai-service": "Azure OpenAI",
    "monitor": "Azure Monitor",
    "azure-monitor": "Azure Monitor",
    "container-instances": "Container Instances",
    "azure-container-instances": "Container Instances",
    "aci": "Container Instances",
    "load-balancer": "Load Balancer",
    "azure-load-balancer": "Load Balancer",
    "virtual-network": "Virtual Network",
    "azure-virtual-network": "Virtual Network",
    "vnet": "Virtual Network",
    "user-assigned-identity": "Azure Active Directory",
    "managed-identity": "Azure Active Directory",
}

_GCP_SERVICE_NAMES = {
    "cloud-storage": "Cloud Storage", "bigquery": "BigQuery",
    "cloud-run": "Cloud Run", "gke": "Kubernetes Engine",
    "cloud-sql": "Cloud SQL", "pubsub": "Cloud Pub/Sub",
    "cloud-functions": "Cloud Functions",
}

# ── Currency conversion ───────────────────────────────────────────────────────
_USD_TO_EUR = 0.92  # approximate conversion factor

# ── Known regions (static — GCP and Azure don't have public unauthenticated APIs) ─
_KNOWN_REGIONS_STATIC: dict[str, list[str]] = {
    "aws": [
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
        "eu-south-1", "eu-south-2", "eu-central-2",
        "ap-southeast-1", "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
        "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
        "ap-south-1", "ap-south-2", "ap-east-1",
        "ca-central-1", "ca-west-1",
        "sa-east-1", "me-south-1", "me-central-1",
        "af-south-1", "il-central-1",
    ],
    "azure": [
        "eastus", "eastus2", "westus", "westus2", "westus3",
        "centralus", "northcentralus", "southcentralus", "westcentralus",
        "westeurope", "northeurope", "francecentral", "francesouth",
        "germanywestcentral", "uksouth", "ukwest",
        "switzerlandnorth", "swedencentral", "norwayeast",
        "austriaeast", "polandcentral", "italynorth", "spaincentral",
        "eastasia", "southeastasia", "japaneast", "japanwest",
        "australiaeast", "australiasoutheast", "australiacentral",
        "brazilsouth", "canadacentral", "canadaeast",
        "southindia", "centralindia", "westindia",
        "koreacentral", "koreasouth", "uaenorth", "southafricanorth",
    ],
    "gcp": [
        "us-central1", "us-east1", "us-east4", "us-east5",
        "us-west1", "us-west2", "us-west3", "us-west4",
        "us-south1", "northamerica-northeast1", "northamerica-northeast2",
        "europe-west1", "europe-west2", "europe-west3", "europe-west4",
        "europe-west6", "europe-west8", "europe-west9", "europe-west10", "europe-west12",
        "europe-north1", "europe-southwest1", "europe-central2",
        "asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2", "asia-northeast3",
        "asia-south1", "asia-south2", "asia-southeast1", "asia-southeast2",
        "australia-southeast1", "australia-southeast2",
        "southamerica-east1", "southamerica-west1",
        "me-west1", "me-central1", "africa-south1",
    ],
}



# ─────────────────────────────────────────────────────────────────────────────
# Tools
@tool
def check_region_availability(candidate: str, region: str, provider: str) -> str:
    """Check if a cloud service is available in the target region.

    Calls official public provider APIs (no credentials required).
    Falls back gracefully if API is unavailable.

    Args:
        candidate: Service name (e.g., "s3", "cloud-storage")
        region: Target region (e.g., "us-east-1", "us-central1", "eastus2")
        provider: Cloud provider ("aws", "gcp", "azure")

    Returns:
        JSON with {available, available_regions, status}
    """
    if not region or not region.strip():
        return json.dumps({"available": True, "available_regions": [], "status": "no-constraint"})

    _FALLBACK = {"available": True, "available_regions": [region], "status": "unknown"}

    cache_key = f"{provider}:{candidate}:{region}"
    if cache_key in _REGION_CACHE:
        return json.dumps(_REGION_CACHE[cache_key])

    if not _HAS_HTTPX:
        logger.warning(f"check_region_availability: httpx not available — fallback for {candidate}")
        return json.dumps(_FALLBACK)

    try:
        result = _FALLBACK.copy()

        if provider == "aws":
            url = "https://api.regional-table.region-services.aws.a2z.com/index.json"
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                prices = resp.json().get("prices", [])
                svc_lower = candidate.lower().replace("-", "").replace("_", "")
                regions_with_svc = []
                for entry in prices:
                    entry_id  = entry.get("id", "").lower()
                    entry_svc = entry_id.split(":")[-1].replace("-", "").replace("_", "")
                    if svc_lower in entry_svc or entry_svc in svc_lower:
                        reg = entry_id.split(":")[0]
                        regions_with_svc.append(reg)
                if regions_with_svc:
                    result["available"]        = region in regions_with_svc
                    result["available_regions"] = regions_with_svc[:20]
                    result["status"]           = "live"

        elif provider == "azure":
            # Step 1: check per-service constraints (service-level availability)
            try:
                from core.region_constraints import check_service_region
                svc_check = check_service_region(candidate, region)
                if not svc_check["available"]:
                    result["available"]          = False
                    result["available_regions"]  = svc_check["supported_regions"]
                    result["recommended_region"] = svc_check["recommended_region"]
                    result["status"]             = "service-constraint"
                    result["reason"]             = svc_check["reason"]
                    _REGION_CACHE[cache_key] = result
                    return json.dumps(result)
            except ImportError:
                pass  # core module unavailable — fall through to generic check

            # Step 2: generic region existence check (is the region itself valid?)
            known = _KNOWN_REGIONS_STATIC.get("azure", [])
            result["available"]        = region in known
            result["available_regions"] = known
            result["status"]           = "static-list"

        elif provider == "gcp":
            known = _KNOWN_REGIONS_STATIC.get("gcp", [])
            result["available"]        = region in known
            result["available_regions"] = known
            result["status"]           = "static-list"

        _REGION_CACHE[cache_key] = result
        return json.dumps(result)

    except Exception as exc:
        logger.warning(
            f"check_region_availability({candidate}, {region}, {provider}): "
            f"API error — {exc} — using fallback"
        )
        return json.dumps(_FALLBACK)


@tool
def check_budget_fit(candidate: str, provider: str, budget_usd_monthly: float) -> str:
    """Check if a cloud service cost fits within the user's monthly budget.

    Calls public pricing APIs (no credentials required).
    Falls back gracefully if API unavailable.

    Args:
        candidate: Service name
        provider: Cloud provider
        budget_usd_monthly: User's max monthly budget (0 = unlimited)

    Returns:
        JSON with {fits, estimated_monthly_usd, margin_pct}
    """
    _FALLBACK = {"fits": True, "estimated_monthly_usd": None, "margin_pct": None, "cost_confidence": "low"}

    if not budget_usd_monthly or budget_usd_monthly <= 0:
        return json.dumps(_FALLBACK)

    cache_key = f"{provider}:{candidate}:budget"
    if cache_key in _PRICING_CACHE:
        cached = _PRICING_CACHE[cache_key].copy()
        cached["fits"] = (cached.get("estimated_monthly_usd") or 0) <= budget_usd_monthly
        if cached.get("estimated_monthly_usd"):
            cached["margin_pct"] = round(
                (budget_usd_monthly - cached["estimated_monthly_usd"]) / budget_usd_monthly * 100, 1
            )
        return json.dumps(cached)

    if not _HAS_HTTPX:
        logger.warning(f"check_budget_fit: httpx not available — fallback for {candidate}")
        return json.dumps(_FALLBACK)

    estimated: float | None = None
    confidence: str = "low"

    try:
        if provider == "aws":
            svc_code = _AWS_PRICING_CODES.get(candidate.lower().replace("-", ""))
            if svc_code:
                url = (
                    f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
                    f"/{svc_code}/current/index.json"
                )
                with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                    resp = client.get(url)
                if resp.status_code == 200:
                    terms = resp.json().get("terms", {}).get("OnDemand", {})
                    for sku_terms in list(terms.values())[:1]:
                        for offer in list(sku_terms.values())[:1]:
                            for pd in list(offer.get("priceDimensions", {}).values())[:1]:
                                price_per_unit = float(pd.get("pricePerUnit", {}).get("USD", 0))
                                estimated  = round(price_per_unit * 730, 2)
                                confidence = "high"

        elif provider == "azure":
            svc_name = _AZURE_PRICING_NAMES.get(candidate.lower(), candidate)
            url = (
                f"https://prices.azure.com/api/retail/prices"
                f"?$filter=serviceName eq '{svc_name}'"
                f" and priceType eq 'Consumption'"
                f" and unitOfMeasure eq '1 Hour'"
                f"&$orderby=retailPrice asc&$top=1"
            )
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                items = resp.json().get("Items", [])
                if items:
                    retail = float(items[0].get("retailPrice", 0))
                    if retail > 0:
                        estimated  = round(retail * 730, 2)
                        confidence = "high"

        elif provider == "gcp":
            llm_cost = _llm_estimate_cost(candidate, "gcp", "")
            if llm_cost is not None:
                estimated  = llm_cost
                confidence = "medium"

    except Exception as exc:
        logger.warning(f"check_budget_fit({candidate}, {provider}): API error — {exc} — using fallback")
        return json.dumps(_FALLBACK)

    if estimated is None:
        return json.dumps(_FALLBACK)

    fits       = estimated <= budget_usd_monthly
    margin_pct = round((budget_usd_monthly - estimated) / budget_usd_monthly * 100, 1)
    result = {
        "fits": fits,
        "estimated_monthly_usd": estimated,
        "margin_pct": margin_pct,
        "cost_confidence": confidence,
    }
    _PRICING_CACHE[cache_key] = result
    return json.dumps(result)


@tool
def check_service_maturity(provider: str, terraform_resource: str) -> str:
    """Assess the maturity of a Terraform resource type via multi-signal GitHub analysis.

    Uses two commit windows (6 months + 2 years) and repo metadata to distinguish
    between genuinely stable/mature resources (many historical commits, zero recent =
    proven and unchanged) versus abandoned or experimental ones.

    Maturity levels and their interpretation:
      mature_stable     (0.92) — long history + no recent changes = proven, production-grade
      mature_maintained (0.82) — long history + occasional updates = active and reliable
      mature_active     (0.65) — long history + frequent recent changes = evolving API, verify compatibility
      established_quiet (0.60) — moderate history + silent recently = likely stable but less proven
      established_maintained (0.75) — moderate history + maintained = good balance
      new_resource      (0.50) — few total commits + some recent activity = emerging resource
      young_or_abandoned(0.20) — very few commits overall = not yet proven or abandoned
      experimental      (0.10) — doc file does not exist = preview/beta resource
      unknown           (0.50) — API error (conservative neutral)

    Args:
        provider:           'aws' | 'azure' (or 'azurerm') | 'gcp' (or 'google')
        terraform_resource: Full Terraform resource type, e.g. 'google_storage_bucket'

    Returns:
        JSON: {maturity_score, level, last_commit_days_ago, commits_6m, commits_2y,
               commits_historical, maturity_breakdown, source}
    """
    normalized = _PROVIDER_ALIASES.get((provider or "").lower().strip())
    if not normalized:
        return json.dumps({
            "error": f"unknown provider '{provider}'",
            "maturity_score": 0.5, "level": "unknown",
            "last_commit_days_ago": None, "commits_6m": 0, "commits_2y": 0,
            "commits_historical": 0, "maturity_breakdown": {}, "source": "input-error",
        })

    resource = (terraform_resource or "").strip()
    prefix   = _PROVIDER_RESOURCE_PREFIXES[normalized]
    svc_slug = resource[len(prefix):] if resource.startswith(prefix) else resource
    svc_slug = svc_slug.lower().replace("-", "_")

    cache_key = f"maturity_v3:{normalized}:{svc_slug}"
    if cache_key in _MATURITY_CACHE:
        return json.dumps(_MATURITY_CACHE[cache_key])

    if not _HAS_HTTPX:
        return json.dumps({
            "error": "httpx unavailable", "maturity_score": 0.5, "level": "unknown",
            "last_commit_days_ago": None, "commits_6m": 0, "commits_2y": 0,
            "commits_historical": 0, "maturity_breakdown": {}, "source": "httpx-missing",
        })

    repo     = _GITHUB_REPOS.get(normalized)
    doc_base = _GITHUB_REPO_DOC_PATHS.get(normalized, "website/docs/r")
    doc_path = f"{doc_base}/{svc_slug}.html.markdown"

    now = datetime.now(tz=timezone.utc)
    six_months_ago = now.timestamp() - 6 * 30 * 86400
    two_years_ago  = now.timestamp() - 2 * 365 * 86400
    since_6m_iso   = datetime.fromtimestamp(six_months_ago, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_2y_iso   = datetime.fromtimestamp(two_years_ago,  tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_GITHUB_HEADERS) as client:
            # Check doc existence
            head_resp = client.get(
                f"https://api.github.com/repos/{repo}/contents/{doc_path}"
            )

            if head_resp.status_code == 404:
                result = {
                    "maturity_score": 0.1, "level": "experimental",
                    "last_commit_days_ago": None, "commits_6m": 0, "commits_2y": 0,
                    "commits_historical": 0,
                    "maturity_breakdown": {"reason": "documentation file not found — resource is preview/beta"},
                    "source": "github-doc-not-found",
                }
                _MATURITY_CACHE[cache_key] = result
                return json.dumps(result)

            if head_resp.status_code == 403:
                return json.dumps({
                    "error": "rate-limit", "maturity_score": 0.5, "level": "unknown",
                    "last_commit_days_ago": None, "commits_6m": 0, "commits_2y": 0,
                    "commits_historical": 0, "maturity_breakdown": {}, "source": "github-rate-limit",
                })

            # 6-month window — detects active changes
            commits_6m_resp = client.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"path": doc_path, "since": since_6m_iso, "per_page": 100},
            )
            # 2-year window — detects proven usage history
            commits_2y_resp = client.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"path": doc_path, "since": since_2y_iso, "per_page": 100},
            )

        if commits_6m_resp.status_code != 200 or commits_2y_resp.status_code != 200:
            bad_code = commits_6m_resp.status_code if commits_6m_resp.status_code != 200 else commits_2y_resp.status_code
            return json.dumps({
                "error": f"github-{bad_code}", "maturity_score": 0.5,
                "level": "unknown", "last_commit_days_ago": None, "commits_6m": 0,
                "commits_2y": 0, "commits_historical": 0, "maturity_breakdown": {},
                "source": f"github-http-{bad_code}",
            })

        commits_6m = commits_6m_resp.json()
        commits_2y = commits_2y_resp.json()
        if not isinstance(commits_6m, list):
            commits_6m = []
        if not isinstance(commits_2y, list):
            commits_2y = []

        count_6m  = len(commits_6m)
        count_2y  = len(commits_2y)
        # commits that happened between 6 months and 2 years ago = historical stability signal
        count_historical = max(0, count_2y - count_6m)

        last_days: int | None = None
        all_recent = commits_6m or commits_2y
        if all_recent:
            date_str = all_recent[0].get("commit", {}).get("author", {}).get("date", "")
            if date_str:
                try:
                    commit_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    last_days = max(0, (now - commit_dt).days)
                except ValueError:
                    pass

        # Multi-signal maturity table:
        # historical commits (6m–2y) reflect long-term proven usage
        # recent commits (0–6m) reflect active maintenance vs. API churn
        if count_historical >= 10 and count_6m == 0:
            score, level = 0.92, "mature_stable"
            reason = f"{count_historical} historical commits, 0 recent — proven stable resource, no API churn"
        elif count_historical >= 10 and 1 <= count_6m <= 5:
            score, level = 0.82, "mature_maintained"
            reason = f"{count_historical} historical + {count_6m} recent commits — mature and actively maintained"
        elif count_historical >= 10 and count_6m > 5:
            score, level = 0.65, "mature_active"
            reason = f"{count_historical} historical + {count_6m} recent commits — mature but API evolving, verify compatibility"
        elif 3 <= count_historical <= 9 and count_6m == 0:
            score, level = 0.60, "established_quiet"
            reason = f"{count_historical} historical commits, 0 recent — established but low activity, likely stable"
        elif 3 <= count_historical <= 9 and count_6m >= 1:
            score, level = 0.75, "established_maintained"
            reason = f"{count_historical} historical + {count_6m} recent commits — established and maintained"
        elif count_historical <= 2 and count_6m == 0:
            score, level = 0.20, "young_or_abandoned"
            reason = f"only {count_historical} historical commits and 0 recent — very new or abandoned resource"
        else:
            score, level = 0.50, "new_resource"
            reason = f"{count_historical} historical + {count_6m} recent commits — emerging resource, limited track record"

        result = {
            "maturity_score":     score,
            "level":              level,
            "last_commit_days_ago": last_days,
            "commits_6m":         count_6m,
            "commits_2y":         count_2y,
            "commits_historical": count_historical,
            "maturity_breakdown": {
                "commits_last_6_months":    count_6m,
                "commits_between_6m_and_2y": count_historical,
                "interpretation":           reason,
            },
            "source": "github-commits",
        }
        _MATURITY_CACHE[cache_key] = result
        return json.dumps(result)

    except Exception as exc:
        logger.warning(f"check_service_maturity({provider}, {terraform_resource}): {exc}")
        return json.dumps({
            "error": str(exc)[:120], "maturity_score": 0.5, "level": "unknown",
            "last_commit_days_ago": None, "commits_6m": 0, "commits_2y": 0,
            "commits_historical": 0, "maturity_breakdown": {}, "source": "github-exception",
        })


def _instance_class_vcores(instance_class: str) -> int:
    """
    Derive vCore count from AWS RDS instance class name algorithmically.
    Pattern: db.<family>.<size> — the size suffix maps to vCore count by AWS convention.
    """
    size = instance_class.lower().split(".")[-1]
    _SIZE_MAP = {
        "micro": 1, "small": 1, "medium": 2, "large": 2,
        "xlarge": 4, "2xlarge": 8, "4xlarge": 16,
        "8xlarge": 32, "12xlarge": 48, "16xlarge": 64,
    }
    return _SIZE_MAP.get(size, 2)


def _azure_postgresql_cost_by_vcores(vcores: int, region: str) -> float | None:
    """
    Query Azure Retail Prices API for PostgreSQL Flexible Server cost
    based on vCore count. Returns monthly EUR cost, or None if API unavailable.

    Azure Retail Prices API uses short SKU names (NOT "Burstable, X vCores"):
      B1MS (1 vCore), B2S (2 vCores), B4ms (4 vCores), B8ms (8 vCores), ...
    Prices are fetched live from prices.azure.com — not hardcoded.
    """
    if not _HAS_HTTPX:
        return None
    azure_region = region.strip() if region.strip() in (
        "westeurope", "northeurope", "eastus", "eastus2", "westus2",
        "francecentral", "francesouth", "uksouth", "ukwest",
        "germanywestcentral", "swedencentral", "norwayeast",
        "switzerlandnorth", "austriaeast", "polandcentral",
        "italynorth", "spaincentral",
    ) else "westeurope"
    _VCORES_TO_SKU = {1: "B1MS", 2: "B2S", 4: "B4ms", 8: "B8ms", 16: "B16ms"}
    sku_name = _VCORES_TO_SKU.get(vcores, f"B{vcores}ms")
    try:
        filter_str = (
            f"serviceName eq 'Azure Database for PostgreSQL'"
            f" and priceType eq 'Consumption'"
            f" and armRegionName eq '{azure_region}'"
            f" and skuName eq '{sku_name}'"
            f" and unitOfMeasure eq '1 Hour'"
        )
        url = f"https://prices.azure.com/api/retail/prices?$filter={filter_str}&$top=5"
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            for item in resp.json().get("Items", []):
                price = float(item.get("retailPrice", 0))
                if price > 0:
                    cost = round(price * 730 * _USD_TO_EUR, 2)
                    logger.info(
                        "_azure_postgresql_cost_by_vcores: %s (%d vCores) = %.2f EUR/mo via Azure API",
                        sku_name, vcores, cost,
                    )
                    return cost
    except Exception as exc:
        logger.warning("_azure_postgresql_cost_by_vcores(%d vCores, %s): %s", vcores, azure_region, exc)
    return None


# ── Free Azure resources — coût = 0€ confirmé par Microsoft ──────────────────
# Ces ressources sont GRATUITES (pas de frais mensuels fixes).
# On ne doit PAS appeler l'API de pricing pour elles — elles ne sont pas listées.
# Source : documentation Azure officielle + Azure Pricing Calculator.
_FREE_AZURE_RESOURCES: frozenset[str] = frozenset({
    # Infrastructure gratuite
    "azurerm_resource_group",
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_network_security_group",
    "azurerm_route_table",
    "azurerm_storage_container",   # inclus dans le storage account
    # IAM / Identité : 0€ (c'est le fait que l'API ne les liste même pas)
    "azurerm_user_assigned_identity",
    "azurerm_role_assignment",
    "azurerm_policy_definition",
    "user-assigned-identity",
    "managed-identity",
    "iam",
    "identity",
    "azure-active-directory",
    # Cognitive Services S0 : tier gratuit jusqu'à 5K appels/mois
    "azurerm_cognitive_account",
    # Monitoring basique
    "azurerm_monitor_metric_alert",  # 10 premières alertes gratuites
})


def _llm_estimate_cost(service: str, cloud_target: str, region: str) -> float | None:
    """
    Use GPT-4o to estimate monthly cloud service cost when public pricing APIs
    are unavailable. Used as last-resort fallback for GCP and failed API calls.

    Retourne None si le service est connu comme étant gratuit (FREE_AZURE_RESOURCES)
    — l'appelant doit alors mettre 0€ sans appel LLM.

    Pour tout autre service inconnu des APIs : appel GPT-4o avec temperature=0.
    Note: même avec temperature=0, le LLM peut varier légèrement entre runs.
    Si la variabilité est problématique, préférer l'API Azure Retail Prices.
    """
    # Ressources gratuites : retourner 0 directement, sans appel LLM ni API
    service_key = (service or "").lower().strip().replace(" ", "-").replace("_", "-")
    for free_key in _FREE_AZURE_RESOURCES:
        norm_key = free_key.lower().replace("_", "-")
        if norm_key in service_key or service_key in norm_key:
            logger.info("_llm_estimate_cost: '%s' est une ressource gratuite → 0€", service)
            return 0.0

    try:
        from langchain_openai import AzureChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        from configuration.settings import settings
        import httpx as _httpx
        llm = AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_AI_ENDPOINT", ""),
            api_key=os.getenv("AZURE_AI_API_KEY", ""),
            azure_deployment=os.getenv("AZURE_MODEL_01") or os.getenv("AZURE_MODEL") or settings.AZURE_MODEL,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION") or settings.AZURE_OPENAI_API_VERSION,
            temperature=0,
            max_tokens=20,
            timeout=_httpx.Timeout(15.0),
        )
        prompt = (
            f"Typical monthly infrastructure cost in EUR for:\n"
            f"  Service: {service}\n  Cloud: {cloud_target}\n  Region: {region or 'eu-west'}\n\n"
            "Reply with ONE number only (EUR/month, pay-as-you-go, small workload). No text."
        )
        resp = llm.invoke([
            SystemMessage(content="Cloud pricing expert. Give realistic pay-as-you-go EUR/month estimates for small workloads."),
            HumanMessage(content=prompt),
        ])
        val = (resp.content or "").strip().replace(",", ".").replace("€", "").replace("EUR", "").split()[0]
        return round(float(val), 2)
    except Exception as exc:
        logger.debug("_llm_estimate_cost(%s, %s): %s", service, cloud_target, exc)
        return None


@tool
def estimate_cost(service: str, region: str, cloud_target: str,
                  instance_class: str = "") -> str:
    """Estimate monthly cost in EUR for a cloud service via public pricing APIs.

    For AWS RDS → Azure PostgreSQL: queries the Azure Retail Prices API using
    the vCore count derived algorithmically from the AWS instance class name.
    For other services: calls provider pricing APIs directly.
    Falls back to GPT-4o estimation when all APIs are unavailable.

    Args:
        service:        Service name (e.g. "s3", "cloud-storage", "blob-storage",
                        "postgresql-flexible-server")
        region:         Target region (e.g. "eu-west-1", "westeurope", "europe-west1")
        cloud_target:   Target cloud — "aws", "azure" or "gcp"
        instance_class: Optional AWS instance class from contextual_hints
                        (e.g. "db.t3.micro") — enables vCore-aware cost estimation.

    Returns:
        JSON: {monthly_cost_eur: float, source: str, confidence: "real"|"estimated",
               instance_class_used: str|null}
    """
    cloud_target   = (cloud_target or "").lower().strip()
    service_norm   = (service or "").lower().replace("_", "-").strip()
    instance_class = (instance_class or "").lower().strip()

    cache_key = f"ecost:{cloud_target}:{service_norm}:{instance_class}"
    if cache_key in _PRICING_CACHE:
        return json.dumps(_PRICING_CACHE[cache_key])

    result: dict = {
        "monthly_cost_eur": None, "source": "unknown",
        "confidence": "estimated", "instance_class_used": instance_class or None,
    }

    # ── Ressources Azure gratuites : 0€ sans appel API ───────────────────────
    # Ces ressources n'apparaissent pas dans l'API Retail Prices car elles sont
    # effectivement gratuites (pas de frais mensuels fixes confirmés par Microsoft).
    service_key_norm = service_norm.replace("_", "-")
    for free_key in _FREE_AZURE_RESOURCES:
        norm_key = free_key.lower().replace("_", "-")
        if norm_key in service_key_norm or service_key_norm in norm_key:
            result["monthly_cost_eur"] = 0.0
            result["source"]           = "azure-free-tier"
            result["confidence"]       = "real"
            _PRICING_CACHE[cache_key]  = result
            logger.info("estimate_cost: '%s' → 0€ (ressource Azure gratuite)", service_norm)
            return json.dumps(result)

    # ── Instance-class-aware cost for RDS → Azure PostgreSQL via Azure API ────
    if instance_class and cloud_target == "azure" and "postgresql" in service_norm:
        vcores    = _instance_class_vcores(instance_class)
        api_cost  = _azure_postgresql_cost_by_vcores(vcores, region)
        if api_cost is not None:
            result["monthly_cost_eur"] = api_cost
            result["source"]           = f"azure-retail-prices-api ({vcores} vCores)"
            result["confidence"]       = "real"
            _PRICING_CACHE[cache_key]  = result
            logger.info(
                "estimate_cost: PostgreSQL %s → %d vCores → %.2f EUR/month (Azure API)",
                instance_class, vcores, api_cost,
            )
            return json.dumps(result)

    if cloud_target == "aws" and _HAS_HTTPX:
        svc_code = _AWS_PRICING_CODES.get(service_norm.replace("-", ""))
        if svc_code:
            try:
                url = (
                    f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
                    f"/{svc_code}/current/index.json"
                )
                with httpx.Client(timeout=20) as client:
                    resp = client.get(url)
                if resp.status_code == 200:
                    terms = resp.json().get("terms", {}).get("OnDemand", {})
                    for sku_terms in list(terms.values())[:1]:
                        for offer in list(sku_terms.values())[:1]:
                            for pd in list(offer.get("priceDimensions", {}).values())[:1]:
                                usd = float(pd.get("pricePerUnit", {}).get("USD", 0))
                                result["monthly_cost_eur"] = round(usd * 730 * _USD_TO_EUR, 2)
                                result["source"]           = "aws-price-list-api"
                                result["confidence"]       = "real"
            except Exception as exc:
                logger.warning("estimate_cost AWS API: %s", exc)

    elif cloud_target == "azure" and _HAS_HTTPX:
        svc_display = _AZURE_PRICING_NAMES.get(service_norm, service)
        # Storage services are priced per GB/transaction, not per hour — skip unitOfMeasure filter
        _STORAGE_SERVICES = {"blob-storage", "azure-blob-storage", "storage-account", "azure-storage", "blob"}
        unit_filter = "" if service_norm in _STORAGE_SERVICES else " and unitOfMeasure eq '1 Hour'"
        try:
            url = (
                f"https://prices.azure.com/api/retail/prices"
                f"?$filter=serviceName eq '{svc_display}'"
                f" and priceType eq 'Consumption'"
                f"{unit_filter}"
                f"&$orderby=retailPrice asc&$top=1"
            )
            with httpx.Client(timeout=20) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                items = resp.json().get("Items", [])
                if items:
                    usd = float(items[0].get("retailPrice", 0))
                    # Storage per-unit prices are tiny (per GB or per 10K ops) — assume 50 GB workload
                    unit = items[0].get("unitOfMeasure", "")
                    if usd > 0:
                        multiplier = 730 if "Hour" in unit else (50 if "GB" in unit else 1)
                        result["monthly_cost_eur"] = round(usd * multiplier * _USD_TO_EUR, 2)
                        result["source"]           = "azure-retail-prices-api"
                        result["confidence"]       = "real"
        except Exception as exc:
            logger.warning("estimate_cost Azure API: %s", exc)

    # ── LLM fallback when all APIs failed or returned nothing (incl. GCP) ─────
    if not result["monthly_cost_eur"]:
        llm_cost = _llm_estimate_cost(service_norm, cloud_target, region)
        if llm_cost is not None:
            result["monthly_cost_eur"] = llm_cost
            result["source"]           = "llm-estimate"
            result["confidence"]       = "estimated"
        else:
            result["monthly_cost_eur"] = 0.0
            result["source"]           = "unavailable"

    _PRICING_CACHE[cache_key] = result
    return json.dumps(result)


@tool
def check_region(service: str, region: str, cloud_target: str) -> str:
    """Check if a cloud service is available in a specific region.

    Calls the AWS Regional Services API for AWS; uses static lists for Azure/GCP
    (their region APIs require authentication).

    Args:
        service: Service name (e.g. "s3", "cloud-run", "blob-storage")
        region: Target region (e.g. "eu-west-1", "westeurope", "europe-west1")
        cloud_target: Target cloud — "aws", "azure" or "gcp"

    Returns:
        JSON: {available: bool, alternatives: list[str], source: str}
    """
    cloud_target = (cloud_target or "").lower().strip()
    region       = (region or "").strip()

    if not region:
        return json.dumps({"available": True, "alternatives": [], "source": "no-constraint"})

    cache_key = f"cregion:{cloud_target}:{service}:{region}"
    if cache_key in _REGION_CACHE:
        return json.dumps(_REGION_CACHE[cache_key])

    result: dict = {"available": True, "alternatives": [], "source": "fallback"}

    if cloud_target == "aws" and _HAS_HTTPX:
        try:
            url = "https://api.regional-table.region-services.aws.a2z.com/index.json"
            with httpx.Client(timeout=20) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                prices   = resp.json().get("prices", [])
                svc_slug = service.lower().replace("-", "").replace("_", "")
                matching: list[str] = []
                for entry in prices:
                    eid       = entry.get("id", "").lower()
                    entry_svc = eid.split(":")[-1].replace("-", "").replace("_", "")
                    if svc_slug in entry_svc or entry_svc in svc_slug:
                        reg = eid.split(":")[0]
                        if reg and reg not in matching:
                            matching.append(reg)
                if matching:
                    available = region in matching
                    eu_alts   = [r for r in matching if r.startswith("eu-")]
                    result = {
                        "available":    available,
                        "alternatives": eu_alts[:5] if not available else [],
                        "source":       "aws-regional-table-api",
                    }
        except Exception as exc:
            logger.warning(f"check_region AWS API: {exc}")

    elif cloud_target == "azure":
        known     = _KNOWN_REGIONS_STATIC["azure"]
        available = region in known
        alts: list[str] = []
        if not available:
            r = region.lower()
            if any(kw in r for kw in ("europe", "france", "uk", "germany", "sweden", "norway", "switzerland")):
                alts = ["westeurope", "northeurope", "francecentral", "uksouth", "germanywestcentral"]
            elif any(kw in r for kw in ("us", "central", "east", "west")):
                alts = ["eastus", "eastus2", "westus2", "centralus"]
            elif any(kw in r for kw in ("asia", "japan", "korea", "australia")):
                alts = ["eastasia", "southeastasia", "japaneast", "australiaeast"]
        result = {"available": available, "alternatives": alts[:4], "source": "azure-static-list"}

    elif cloud_target == "gcp":
        known     = _KNOWN_REGIONS_STATIC["gcp"]
        available = region in known
        alts = []
        if not available:
            r = region.lower()
            if "europe" in r:
                alts = ["europe-west1", "europe-west2", "europe-west3", "europe-west4"]
            elif "us" in r or "america" in r:
                alts = ["us-central1", "us-east1", "us-west1"]
            elif "asia" in r or "australia" in r:
                alts = ["asia-east1", "asia-southeast1", "australia-southeast1"]
        result = {"available": available, "alternatives": alts[:4], "source": "gcp-static-list"}

    _REGION_CACHE[cache_key] = result
    return json.dumps(result)
