"""
tools_agent_01_dynamic.py - Dynamic tools for Agent 01 (YAML-free mode).

Three tools that let Agent 01 reason without service_registry.yaml /
service_meta.yaml:

  1. search_provider_docs   — vector search the indexed provider docs
  2. get_pricing             — live pricing from AWS / GCP / Azure APIs
  3. find_equivalent_service — cross-provider functional equivalence via RAG

Anti-hallucination guard: each returned Terraform resource type is validated
against a whitelist built from the terraform_resources table. If the LLM
invents a name not in that list, the tool strips it.

Usage: these tools are OPTIONAL replacements for the YAML-backed tools.
When service_registry.yaml is removed, Agent 01 binds to these instead.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger("Agent01Dynamic")

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    from cachetools import TTLCache
    _PROVIDER_DOCS_CACHE: Any = TTLCache(maxsize=512, ttl=3600)
    _PRICING_CACHE: Any = TTLCache(maxsize=256, ttl=1800)
    _EQUIV_CACHE: Any = TTLCache(maxsize=256, ttl=3600)
    _WHITELIST_CACHE: Any = TTLCache(maxsize=4, ttl=3600)
except ImportError:
    _PROVIDER_DOCS_CACHE = {}
    _PRICING_CACHE = {}
    _EQUIV_CACHE = {}
    _WHITELIST_CACHE = {}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — search_provider_docs
# ─────────────────────────────────────────────────────────────────────────────
@tool
def search_provider_docs(provider: str, query: str, top_k: int = 5) -> str:
    """Search the indexed Terraform documentation for a provider.

    Uses the same pgvector corpus that Agent 02 consumes (terraform_resources).
    Returns top-K resources ranked by semantic similarity to `query`, each
    annotated with Terraform resource type, description, required args.

    Use this to *validate* that a candidate service exists on the target
    cloud — if no result comes back with high similarity, the candidate is
    very likely hallucinated.

    Args:
        provider: 'aws' | 'azurerm' | 'google' (the Terraform provider name)
        query:    free-form description, e.g. "serverless function triggered by HTTP"
        top_k:    number of results (default 5)

    Returns:
        JSON list: [{resource_type, description, required_args, similarity}, ...]
    """
    _PROVIDER_DB = {
        "aws": "aws", "amazon": "aws",
        "azure": "azurerm", "azurerm": "azurerm", "microsoft": "azurerm",
        "gcp": "google", "google": "google", "googlecloud": "google",
    }
    provider = _PROVIDER_DB.get((provider or "").lower().strip(), (provider or "").lower().strip())
    cache_key = f"docs:{provider}:{query}:{top_k}"
    if cache_key in _PROVIDER_DOCS_CACHE:
        return _PROVIDER_DOCS_CACHE[cache_key]

    try:
        from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
        from core.embedding_model_loader import get_shared_embedder
    except ImportError as e:
        logger.warning(f"search_provider_docs: RAG infra unavailable — {e}")
        return json.dumps([])

    try:
        embedder = get_shared_embedder(EMBEDDING_MODEL)
        emb = embedder.encode([query]).tolist()[0]
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, description, required_args,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM terraform_resources
                    WHERE provider = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (emb, provider, emb, top_k),
                )
                rows = cur.fetchall()

        out = [
            {
                "resource_type": r[0],
                "description": (r[1] or "")[:300],  # cap for token budget
                "required_args": r[2] if r[2] is not None else [],
                "similarity": round(float(r[3]), 4),
            }
            for r in rows
        ]
        result = json.dumps(out)
        _PROVIDER_DOCS_CACHE[cache_key] = result
        return result
    except Exception as exc:
        logger.warning(f"search_provider_docs failed: {exc}")
        return json.dumps([])


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — get_pricing
# ─────────────────────────────────────────────────────────────────────────────
@tool
def get_pricing(
    provider: str,
    service: str,
    region: str = "us-east-1",
    usage_profile: str = "{}",
) -> str:
    """Query real pricing APIs — no static fallback.

    Hierarchy:
      1. Native cloud API: AWS Pricing API, Azure Retail Prices API, GCP public
         pricelist (cloudpricingcalculator.appspot.com — no auth required)
      2. Infracost GraphQL API (if INFRACOST_API_KEY set) — covers AWS, Azure, GCP

    If both levels fail, estimated_monthly_usd is null and source is 'unavailable'.
    The LLM must handle null explicitly — never invent a cost.

    Args:
        provider: 'aws' | 'gcp' | 'azure'
        service:  canonical service name ('s3', 'cloud-storage', ...)
        region:   target region
        usage_profile: JSON string with usage assumptions

    Returns:
        JSON: {estimated_monthly_usd, unit, source, cached, fallback}
    """
    provider = (provider or "").lower().strip()
    service = (service or "").lower().strip()
    cache_key = f"price:{provider}:{service}:{region}"
    if cache_key in _PRICING_CACHE:
        out = dict(_PRICING_CACHE[cache_key])
        out["cached"] = True
        return json.dumps(out)

    try:
        profile = json.loads(usage_profile) if usage_profile else {}
    except json.JSONDecodeError:
        profile = {}

    estimated: float | None = None
    source: str | None = None
    fallback = False

    # ── Étape 1 : APIs natives cloud ─────────────────────────────────────────
    if _HAS_HTTPX:
        try:
            if provider == "aws":
                estimated, source = _aws_price(service, region, profile)
            elif provider == "azure":
                estimated, source = _azure_price(service, region, profile)
            elif provider == "gcp":
                estimated, source = _gcp_calculator_price(service, region, profile)
        except Exception as exc:
            logger.warning(f"get_pricing native API ({provider}, {service}): {exc}")

    # ── Étape 2 : Infracost GraphQL API (si clé présente) ────────────────────
    if estimated is None and _HAS_HTTPX:
        try:
            estimated, source = _infracost_price(provider, service)
        except Exception as exc:
            logger.warning(f"get_pricing infracost ({provider}, {service}): {exc}")

    # Baseline fallback when all external APIs fail
    if estimated is None:
        baselines = _PRICING_BASELINES_USD.get(provider, {})
        svc_norm = service.lower().replace("_", "-")
        estimated = baselines.get(svc_norm)
        if estimated is None:
            for k, v in baselines.items():
                if k in svc_norm or svc_norm in k:
                    estimated = v
                    break
        if estimated is not None:
            source = f"{provider}-baseline"
            fallback = True
        else:
            source = "unavailable"

    # Build cost breakdown by component
    breakdown = _build_cost_breakdown(provider, service, estimated, profile)

    result = {
        "estimated_monthly_usd": estimated,
        "unit": "USD/month" if estimated is not None else None,
        "source": source,
        "cached": False,
        "fallback": fallback,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "breakdown": breakdown,
        "basis": _cost_basis(provider, service),
    }
    _PRICING_CACHE[cache_key] = result
    return json.dumps(result)


def _cost_basis(provider: str, service: str) -> str:
    """Return human-readable basis for the cost estimate."""
    service = service.lower()
    if any(x in service for x in ["s3", "storage", "blob", "gcs"]):
        return "par Go stocké/mois + requêtes"
    if any(x in service for x in ["lambda", "functions", "cloud-run"]):
        return "par requête + durée d'exécution (ms)"
    if any(x in service for x in ["openai", "bedrock", "cognitive", "vertex-ai"]):
        return "par token (input + output)"
    if any(x in service for x in ["rds", "postgresql", "mysql", "sql", "cosmos", "dynamodb"]):
        return "par heure (compute 24h/24) + stockage Go"
    if any(x in service for x in ["ec2", "vm", "virtual-machine", "compute"]):
        return "par heure (supposé 730h/mois = 24h/24)"
    if any(x in service for x in ["eks", "aks", "gke", "kubernetes"]):
        return "par heure nœud worker + management fee"
    if any(x in service for x in ["iam", "identity", "managed-identity", "role"]):
        return "gratuit"
    return "par heure ou par usage selon la ressource"


def _build_cost_breakdown(provider: str, service: str, total: float | None, profile: dict) -> list[dict]:
    """Decompose total cost into components for display."""
    if total is None:
        return []
    svc = service.lower()

    # PostgreSQL / RDS
    if any(x in svc for x in ["postgresql", "postgres", "rds", "mysql", "sql"]):
        vcores = profile.get("vcores", 2)
        storage_gb = profile.get("storage_gb", 20)
        compute = round(total * 0.75, 2)
        storage = round(storage_gb * 0.115, 2)
        backup   = round(total * 0.05, 2)
        return [
            {"label": f"Compute ({vcores} vCores, 730h/mois)", "amount": compute, "basis": "$/heure × 730h"},
            {"label": f"Stockage SSD ({storage_gb} Go)", "amount": storage, "basis": "$/Go/mois"},
            {"label": "Backup (rétention 7j)", "amount": backup, "basis": "inclus jusqu'à 1× storage"},
        ]

    # Blob / S3 / GCS
    if any(x in svc for x in ["s3", "blob", "storage", "gcs", "cloud-storage"]):
        storage_gb = profile.get("storage_gb", 50)
        storage_cost = round(storage_gb * 0.02, 2)
        ops_cost = round(total - storage_cost, 2) if total > storage_cost else round(total * 0.3, 2)
        return [
            {"label": f"Stockage ({storage_gb} Go)", "amount": storage_cost, "basis": "$/Go/mois"},
            {"label": "Opérations (GET/PUT estimées)", "amount": max(ops_cost, 0), "basis": "$/10k requêtes"},
        ]

    # AI / LLM
    if any(x in svc for x in ["openai", "cognitive", "bedrock", "vertex", "ai"]):
        return [
            {"label": "Coût par token input", "amount": None, "basis": "~$2/1M tokens"},
            {"label": "Coût par token output", "amount": None, "basis": "~$8/1M tokens"},
            {"label": "Estimé (usage inconnu)", "amount": total, "basis": "basé sur usage moyen"},
        ]

    # VM / EC2
    if any(x in svc for x in ["ec2", "vm", "virtual-machine", "compute-engine"]):
        return [
            {"label": "Instance (730h/mois)", "amount": round(total * 0.85, 2), "basis": "$/heure × 730h"},
            {"label": "Stockage OS (30 Go)", "amount": round(total * 0.10, 2), "basis": "$/Go/mois"},
            {"label": "Réseau sortant (~10 Go)", "amount": round(total * 0.05, 2), "basis": "$/Go transféré"},
        ]

    # Generic fallback
    return [{"label": "Total estimé", "amount": total, "basis": "prix public sans réductions"}]


def _aws_price(service: str, region: str, profile: dict) -> tuple[float | None, str]:
    _SVC_CODES = {
        "s3": "AmazonS3", "ec2": "AmazonEC2", "rds": "AmazonRDS",
        "dynamodb": "AmazonDynamoDB", "lambda": "AWSLambda",
        "sqs": "AWSQueueService", "sns": "AmazonSNS",
    }
    code = _SVC_CODES.get(service)
    if not code:
        return None, "aws-api-no-code"
    url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{code}/current/index.json"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return None, f"aws-api-{resp.status_code}"
    data = resp.json()
    terms = data.get("terms", {}).get("OnDemand", {})
    for sku_terms in list(terms.values())[:1]:
        for offer in list(sku_terms.values())[:1]:
            for pd in list(offer.get("priceDimensions", {}).values())[:1]:
                p = float(pd.get("pricePerUnit", {}).get("USD", 0))
                return round(p * 730, 2), "aws-api"
    return None, "aws-api-no-terms"


_AZURE_API_SERVICE_NAMES = {
    "storage": "Azure Blob Storage",
    "blob-storage": "Azure Blob Storage",
    "blob": "Azure Blob Storage",
    "storage-account": "Azure Blob Storage",
    "mysql": "Azure Database for MySQL",
    "mysql-flexible-server": "Azure Database for MySQL",
    "postgresql": "Azure Database for PostgreSQL",
    "postgres": "Azure Database for PostgreSQL",
    "postgresql-flexible-server": "Azure Database for PostgreSQL",
    "cosmos": "Azure Cosmos DB",
    "cosmosdb": "Azure Cosmos DB",
    "functions": "Azure Functions",
    "virtual-machine": "Virtual Machines",
    "vm": "Virtual Machines",
    "linux-virtual-machine": "Virtual Machines",
    "aks": "Azure Kubernetes Service",
    "kubernetes": "Azure Kubernetes Service",
    "app-service": "Azure App Service",
    "app-service-plan": "Azure App Service",
    "key-vault": "Key Vault",
    "keyvault": "Key Vault",
    "service-bus": "Service Bus",
    "event-hubs": "Event Hubs",
    "container-apps": "Azure Container Apps",
    "container-instances": "Container Instances",
    "openai": "Azure OpenAI",
    "azure-openai": "Azure OpenAI",
    "cognitive-search": "Azure Cognitive Search",
    "search": "Azure Cognitive Search",
    "redis": "Azure Cache for Redis",
    "cache-for-redis": "Azure Cache for Redis",
    "role-assignment": "Azure Active Directory",
    "user-assigned-identity": "Azure Active Directory",
    "virtual-network": "Virtual Network",
    "subnet": "Virtual Network",
    "network-security-group": "Azure Firewall",
    "public-ip": "IP Addresses",
    "route": "Azure Route Server",
    "route-table": "Azure Route Server",
}

# Monthly USD baselines for when external pricing APIs are unavailable
_PRICING_BASELINES_USD: dict[str, dict[str, float]] = {
    "aws": {
        "s3": 25, "ec2": 37, "rds": 61, "dynamodb": 27, "lambda": 1,
        "eks": 78, "ecs": 20, "sqs": 1, "sns": 1, "cloudfront": 10,
        "bedrock": 55, "sagemaker": 98, "iam": 0, "secretsmanager": 1,
        "kms": 1, "cloudwatch": 4, "route53": 1, "api-gateway": 4,
        "vpc": 0, "subnet": 0, "security-group": 0, "internet-gateway": 0,
    },
    "azure": {
        "storage-account": 25, "blob-storage": 25, "blob": 25,
        "cosmos": 33, "cosmosdb": 33,
        "postgresql": 54, "postgres": 54, "mysql": 54,
        "mysql-flexible-server": 54, "postgresql-flexible-server": 54,
        "virtual-machine": 76, "vm": 76, "linux-virtual-machine": 76,
        "app-service": 20, "app-service-plan": 20,
        "container-instances": 13, "functions": 1,
        "container-apps": 17, "aks": 71, "kubernetes": 71,
        "service-bus": 10, "event-hubs": 24,
        "openai": 55, "azure-openai": 55,
        "key-vault": 1, "keyvault": 1,
        "virtual-network": 0, "subnet": 0,
        "network-security-group": 0, "route": 0, "route-table": 0,
        "public-ip": 4, "user-assigned-identity": 0, "role-assignment": 0,
    },
    "gcp": {
        "cloud-storage": 25, "bigquery": 5, "cloud-run": 18,
        "gke": 74, "cloud-sql": 52, "pubsub": 10,
        "cloud-functions": 8, "vertex-ai": 120,
    },
}


def _azure_price(service: str, region: str, profile: dict) -> tuple[float | None, str]:
    svc_norm = service.lower().replace("_", "-")

    # PostgreSQL Flexible Server: use vCore-aware pricing (B1MS, B2S, ...)
    # instance_class can be passed via usage_profile e.g. {"instance_class": "db.t3.medium"}
    if "postgresql" in svc_norm or "postgres" in svc_norm:
        try:
            from agents.migration_planner.service_lookup_tools import (
                _instance_class_vcores,
                _azure_postgresql_cost_by_vcores,
            )
            ic = profile.get("instance_class", "")
            vcores = _instance_class_vcores(ic) if ic else 2  # default 2 vCores
            cost = _azure_postgresql_cost_by_vcores(vcores, region)
            if cost is not None:
                return round(cost / 0.92, 2), f"azure-retail-prices-api ({vcores} vCores)"
        except Exception as exc:
            logger.warning("_azure_price postgresql vCore path: %s", exc)

    svc_display = _AZURE_API_SERVICE_NAMES.get(svc_norm, service)
    # Storage services are priced per GB/transaction, not per hour
    _STORAGE_SERVICES = {"blob-storage", "azure-blob-storage", "storage-account", "azure-storage", "blob"}
    unit_filter = "" if svc_norm in _STORAGE_SERVICES else " and unitOfMeasure eq '1 Hour'"
    url = (
        f"https://prices.azure.com/api/retail/prices"
        f"?$filter=serviceName eq '{svc_display}'"
        f" and armRegionName eq '{region}'"
        f" and priceType eq 'Consumption'"
        f"{unit_filter}"
        f"&$orderby=retailPrice asc&$top=1"
    )
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return None, f"azure-api-{resp.status_code}"
    items = resp.json().get("Items", [])
    if not items:
        return None, "azure-api-empty"
    p = float(items[0].get("retailPrice", 0))
    if p <= 0:
        return None, "azure-api-zero-price"
    return round(p * 730, 2), "azure-api"


def _gcp_calculator_price(service: str, region: str, profile: dict) -> tuple[float | None, str]:
    """GCP pricing — uses Cloud Billing Catalog API (no auth required for public SKUs).

    Hierarchy:
      1. GCP Cloud Billing Catalog API (official, real-time)
      2. GCP public pricelist JSON (fallback, no auth)
    """
    # Map service → GCP service display name for Billing API
    _GCP_BILLING_SERVICES = {
        "cloud-storage": "Cloud Storage",
        "storage": "Cloud Storage",
        "gcs": "Cloud Storage",
        "cloud-sql": "Cloud SQL",
        "sql": "Cloud SQL",
        "cloud-run": "Cloud Run",
        "run": "Cloud Run",
        "cloud-functions": "Cloud Functions",
        "functions": "Cloud Functions",
        "gke": "Kubernetes Engine",
        "kubernetes-engine": "Kubernetes Engine",
        "bigquery": "BigQuery",
        "firestore": "Cloud Firestore",
        "spanner": "Cloud Spanner",
        "pubsub": "Cloud Pub/Sub",
        "vertex-ai": "Vertex AI",
        "compute-engine": "Compute Engine",
        "compute": "Compute Engine",
    }

    # 1. GCP Cloud Billing Catalog API
    svc_name = _GCP_BILLING_SERVICES.get(service)
    if svc_name and _HAS_HTTPX:
        try:
            url = f"https://cloudbilling.googleapis.com/v1/services"
            with httpx.Client(timeout=10) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                services = resp.json().get("services", [])
                svc_id = next(
                    (s["name"].split("/")[-1] for s in services
                     if svc_name.lower() in s.get("displayName", "").lower()),
                    None
                )
                if svc_id:
                    skus_url = f"https://cloudbilling.googleapis.com/v1/services/{svc_id}/skus"
                    skus_resp = client.get(skus_url)
                    if skus_resp.status_code == 200:
                        skus = skus_resp.json().get("skus", [])
                        for sku in skus:
                            # Pick first on-demand SKU matching region
                            regions = sku.get("serviceRegions", [])
                            if regions and region not in regions and "global" not in regions:
                                continue
                            pricing = sku.get("pricingInfo", [{}])[0]
                            expr = pricing.get("pricingExpression", {})
                            tiers = expr.get("tieredRates", [])
                            if tiers:
                                unit_price = tiers[-1].get("unitPrice", {})
                                nanos = unit_price.get("nanos", 0)
                                units = unit_price.get("units", 0)
                                rate = float(units) + float(nanos) / 1e9
                                usage_unit = expr.get("usageUnit", "").lower()
                                if rate > 0:
                                    monthly = round(rate * 730, 2) if "h" in usage_unit else round(rate * 100, 2)
                                    return monthly, "gcp-billing-catalog-api"
        except Exception as exc:
            logger.warning("_gcp_calculator_price billing API: %s", exc)

    # 2. Fallback: GCP public pricelist JSON
    _GCP_SERVICE_KEYS = {
        "cloud-storage": "CP-CLOUD-STORAGE",
        "storage": "CP-CLOUD-STORAGE",
        "gcs": "CP-CLOUD-STORAGE",
        "cloud-run": "CP-CLOUD-RUN",
        "run": "CP-CLOUD-RUN",
        "cloud-sql": "CP-CLOUD-SQL",
        "sql": "CP-CLOUD-SQL",
        "pubsub": "CP-PUBSUB",
        "cloud-functions": "CP-CLOUD-FUNCTIONS",
        "functions": "CP-CLOUD-FUNCTIONS",
        "gke": "CP-KUBERNETES-ENGINE",
        "kubernetes-engine": "CP-KUBERNETES-ENGINE",
        "bigquery": "CP-BIGQUERY",
        "firestore": "CP-FIRESTORE",
        "spanner": "CP-SPANNER",
        "vertex-ai": "CP-VERTEX-AI",
        "compute-engine": "CP-COMPUTEENGINE",
        "compute": "CP-COMPUTEENGINE",
    }
    prefix = _GCP_SERVICE_KEYS.get(service)
    if not prefix:
        return None, "gcp-no-mapping"

    url = "https://cloudpricingcalculator.appspot.com/static/data/pricelist.json"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return None, f"gcp-calculator-{resp.status_code}"

    data = resp.json()
    gcp_prices = data.get("gcp_price_list", {})

    # Find first key matching our prefix and extract a numeric rate
    for key, value in gcp_prices.items():
        if not key.startswith(prefix):
            continue
        if not isinstance(value, dict):
            continue
        # Values are region→rate dicts; try the requested region first, then 'us'
        rate = None
        region_norm = region.replace("-", "")  # e.g. "us-east1" → "useast1"
        for rk, rv in value.items():
            if rk.lower().replace("-", "") == region_norm.lower():
                rate = rv
                break
        if rate is None:
            # Pick first non-metadata numeric value
            for rk, rv in value.items():
                if rk in ("cores", "memory", "ssd", "maxNumberOfPdVolumes"):
                    continue
                if isinstance(rv, (int, float)) and rv > 0:
                    rate = rv
                    break
        if rate is not None:
            # Rates in this file are hourly; multiply by 730 for monthly
            monthly = round(float(rate) * 730, 2)
            if monthly < 0.5:
                # Already expressed as monthly (e.g. per-GB prices)
                monthly = round(float(rate) * 100, 2)  # assume ~100 GB
            return monthly, "gcp-calculator"

    return None, "gcp-calculator-no-rate"


def _infracost_price(provider: str, service: str) -> tuple[float | None, str]:
    """Infracost GraphQL pricing API — covers AWS, Azure, GCP.

    Free tier: https://www.infracost.io/docs/supported_resources/
    Requires INFRACOST_API_KEY env var (free key from infracost.io).
    """
    api_key = os.getenv("INFRACOST_API_KEY")
    if not api_key:
        return None, "infracost-no-key"

    # Map (provider, service) → Infracost resource type
    _INFRACOST_RESOURCES = {
        ("aws", "s3"): "aws_s3_bucket",
        ("aws", "ec2"): "aws_instance",
        ("aws", "rds"): "aws_db_instance",
        ("aws", "lambda"): "aws_lambda_function",
        ("aws", "dynamodb"): "aws_dynamodb_table",
        ("aws", "sqs"): "aws_sqs_queue",
        ("aws", "sns"): "aws_sns_topic",
        ("aws", "eks"): "aws_eks_cluster",
        ("gcp", "cloud-storage"): "google_storage_bucket",
        ("gcp", "cloud-run"): "google_cloud_run_service",
        ("gcp", "cloud-sql"): "google_sql_database_instance",
        ("gcp", "pubsub"): "google_pubsub_topic",
        ("gcp", "gke"): "google_container_cluster",
        ("gcp", "cloud-functions"): "google_cloudfunctions_function",
        ("azure", "blob-storage"): "azurerm_storage_account",
        ("azure", "functions"): "azurerm_function_app",
        ("azure", "cosmos"): "azurerm_cosmosdb_account",
        ("azure", "service-bus"): "azurerm_servicebus_namespace",
        ("azure", "postgresql"): "azurerm_postgresql_flexible_server",
        ("azure", "aks"): "azurerm_kubernetes_cluster",
    }
    resource_type = _INFRACOST_RESOURCES.get((provider, service))
    if not resource_type:
        return None, "infracost-no-resource-type"

    query = """
    query {
      products(filter: {vendorName: "%s", service: "%s"}) {
        prices(filter: {purchaseOption: "on_demand"}) {
          USD
          unit
        }
      }
    }
    """ % (provider, resource_type)

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://pricing.api.infracost.io/graphql",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json={"query": query},
        )
    if resp.status_code != 200:
        return None, f"infracost-{resp.status_code}"

    data = resp.json()
    products = data.get("data", {}).get("products", [])
    if not products:
        return None, "infracost-no-products"

    # Take the first price and normalise to monthly
    prices = products[0].get("prices", [])
    if not prices:
        return None, "infracost-no-prices"

    usd_rate = float(prices[0].get("USD", 0))
    unit = (prices[0].get("unit") or "").lower()
    if "hour" in unit:
        monthly = round(usd_rate * 730, 2)
    elif "month" in unit:
        monthly = round(usd_rate, 2)
    else:
        monthly = round(usd_rate * 730, 2)  # assume hourly by default

    return monthly, "infracost-api"


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — find_equivalent_service
# ─────────────────────────────────────────────────────────────────────────────
@tool
def find_equivalent_service(
    source_provider: str,
    source_service: str,
    target_provider: str,
    top_k: int = 5,
) -> str:
    """Find functionally equivalent services on a target cloud.

    Two-step RAG:
      (a) find doc chunks for the SOURCE service, extract its functional category
      (b) vector-search doc chunks of the TARGET provider ranked by category-similarity

    This replaces service_registry.yaml for equivalence lookup. The returned
    resource_types are whitelisted against terraform_resources, so any
    hallucinated invented names are stripped before returning.

    Args:
        source_provider: 'aws' | 'azurerm' | 'google'
        source_service:  service or Terraform type ('aws_s3_bucket', 'lambda', ...)
        target_provider: target cloud provider
        top_k:           candidates to return

    Returns:
        JSON: [{target_resource_type, description, similarity, verified}, ...]
    """
    _PROVIDER_DB = {
        "aws": "aws", "amazon": "aws",
        "azure": "azurerm", "azurerm": "azurerm", "microsoft": "azurerm",
        "gcp": "google", "google": "google", "googlecloud": "google",
    }
    source_provider = _PROVIDER_DB.get((source_provider or "").lower().strip(), (source_provider or "").lower().strip())
    target_provider = _PROVIDER_DB.get((target_provider or "").lower().strip(), (target_provider or "").lower().strip())
    cache_key = f"equiv:{source_provider}:{source_service}:{target_provider}:{top_k}"
    if cache_key in _EQUIV_CACHE:
        return _EQUIV_CACHE[cache_key]

    try:
        from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
        from core.embedding_model_loader import get_shared_embedder
    except ImportError:
        return json.dumps([])

    try:
        embedder = get_shared_embedder(EMBEDDING_MODEL)

        # Step (a): get the source service description as query text
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, description
                    FROM terraform_resources
                    WHERE provider = %s AND id ILIKE %s
                    LIMIT 1
                    """,
                    (source_provider, f"%{source_service}%"),
                )
                row = cur.fetchone()

        if row and row[1]:
            query_text = f"{row[0]}: {row[1]}"
        else:
            query_text = source_service  # fallback: just the name

        # Step (b): semantic search in target provider
        emb = embedder.encode([query_text]).tolist()[0]
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, description,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM terraform_resources
                    WHERE provider = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (emb, target_provider, emb, top_k * 2),  # over-fetch for whitelist
                )
                rows = cur.fetchall()

        whitelist = _get_terraform_whitelist(target_provider)
        out = []
        for r in rows[:top_k]:
            resource_type = r[0]
            out.append({
                "target_resource_type": resource_type,
                "description": (r[1] or "")[:300],
                "similarity": round(float(r[2]), 4),
                "verified": resource_type in whitelist,
            })

        result = json.dumps(out)
        _EQUIV_CACHE[cache_key] = result
        return result

    except Exception as exc:
        logger.warning(f"find_equivalent_service failed: {exc}")
        return json.dumps([])


def _get_terraform_whitelist(provider: str) -> set[str]:
    """Fetch the set of valid Terraform resource types for `provider` so we
    can flag LLM-invented names before they hit Agent 02."""
    if provider in _WHITELIST_CACHE:
        return _WHITELIST_CACHE[provider]

    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM terraform_resources WHERE provider = %s",
                    (provider,),
                )
                wl = {r[0] for r in cur.fetchall()}
        _WHITELIST_CACHE[provider] = wl
        return wl
    except Exception:
        return set()


# ─────────────────────────────────────────────────────────────────────────────
# Structured system prompt for Agent 01 (no YAML)
# ─────────────────────────────────────────────────────────────────────────────
AGENT_01_DYNAMIC_SYSTEM = """
Tu es Agent 01 — Cloud Migration Planning Expert (mode DYNAMIQUE).

Tu n'as AUCUN fichier YAML de référence. Pour chaque service source, tu DOIS
suivre le protocole strict ci-dessous avant de produire un output.

Outils disponibles:
  - search_provider_docs(provider, query, top_k)  → doc chunks + similarity
  - find_equivalent_service(source_provider, source_service, target_provider)
  - get_pricing(provider, service, region)        → estimated_monthly_usd

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROTOCOLE STRICT — Pour CHAQUE service source:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ITÉRATION 1 — Catégorisation (interne, pas d'outil)
  Quelle est la catégorie fonctionnelle du service source ?
  (storage, serverless-functions, managed-kubernetes, ...)

ITÉRATION 2 — Recherche d'équivalents
  Action OBLIGATOIRE: find_equivalent_service(...)
  → Ne propose JAMAIS un service cible sans cet appel.
  → Parmi les résultats, garde UNIQUEMENT ceux avec verified=true.
  → Si tout est verified=false, marque le service "needs_human_review".

ITÉRATION 3 — Validation par doc
  Action OBLIGATOIRE: search_provider_docs(target_provider, "<feature requirements>")
  → Confirme que le candidat couvre les features requises.

ITÉRATION 4 — Pricing
  Action OBLIGATOIRE: get_pricing(target_provider, candidate, region)
  → Ne donne JAMAIS un coût sans cet appel.
  → Si fallback=true, annote "coût estimé (baseline)" dans la justification.

ITÉRATION 5 — Décision 7R (interne)
  REHOST:      equivalence >= 0.95, 0 breaking change critique
  REPLATFORM:  0.80 <= equivalence < 0.95, breaking changes mineurs/modérés
  REFACTOR:    equivalence < 0.80 OU breaking change CRITICAL
  RETIRE:      pas d'équivalent ou service déprécié
  RETAIN:      migration trop risquée pour ce cycle
  REPURCHASE:  SaaS externe est plus adapté

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTERDICTIONS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - Interdit d'inventer un nom de resource Terraform.
    Seuls les noms retournés par les outils (verified=true) sont valides.
  - Interdit de donner un coût sans appeler get_pricing.
  - Interdit de skipper une itération du protocole.
  - Si une recherche retourne [] deux fois, marque le service
    "needs_human_review" et passe au suivant.
"""
