"""
services/infra_monitor.py — Azure infrastructure health monitoring.

Reads terraform.tfstate from the migration's runs directory,
calls Azure Resource Manager to check provisioning state for each resource,
fetches Azure Monitor metrics (CPU, memory, connections…),
and detects configuration drift between tfstate and live Azure state.

All Azure SDK calls are wrapped in try/except so the module loads even when
azure-mgmt-resource or azure-mgmt-monitor are not installed.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("InfraMonitor")

# ── ARM API versions per Terraform resource type ─────────────────────────────
_ARM_API_VERSIONS: dict[str, str] = {
    "azurerm_resource_group":                        "2021-04-01",
    "azurerm_storage_account":                       "2023-01-01",
    "azurerm_storage_container":                     "2023-01-01",
    "azurerm_postgresql_flexible_server":            "2023-06-01-preview",
    "azurerm_mysql_flexible_server":                 "2023-06-30",
    "azurerm_mssql_server":                          "2022-11-01-preview",
    "azurerm_mssql_database":                        "2022-11-01-preview",
    "azurerm_user_assigned_identity":                "2023-01-31",
    "azurerm_cognitive_account":                     "2023-05-01",
    "azurerm_application_insights":                  "2020-02-02",
    "azurerm_search_service":                        "2023-11-01",
    "azurerm_machine_learning_workspace":            "2024-01-01-preview",
    "azurerm_container_registry":                    "2023-07-01",
    "azurerm_kubernetes_cluster":                    "2024-01-01",
    "azurerm_service_plan":                          "2023-01-01",
    "azurerm_linux_function_app":                    "2023-01-01",
    "azurerm_linux_web_app":                         "2023-01-01",
    "azurerm_windows_web_app":                       "2023-01-01",
    "azurerm_virtual_network":                       "2023-09-01",
    "azurerm_subnet":                                "2023-09-01",
    "azurerm_network_security_group":                "2023-09-01",
    "azurerm_private_endpoint":                      "2023-09-01",
    "azurerm_private_dns_zone":                      "2020-06-01",
    "azurerm_private_dns_zone_virtual_network_link": "2020-06-01",
    "azurerm_log_analytics_workspace":               "2023-09-01",
    "azurerm_key_vault":                             "2023-07-01",
    "azurerm_cosmosdb_account":                      "2024-02-15-preview",
    "azurerm_container_group":                       "2023-05-01",
    "azurerm_eventhub_namespace":                    "2023-01-01-preview",
}
_DEFAULT_API_VERSION = "2022-09-01"

# ── Provisioning state → health status ────────────────────────────────────────
_STATE_MAP: dict[str, str] = {
    "Succeeded":  "healthy",
    "Running":    "healthy",
    "Available":  "healthy",
    "Online":     "healthy",
    "Ready":      "healthy",
    "Updating":   "updating",
    "Creating":   "creating",
    "Deleting":   "deleting",
    "Moving":     "updating",
    "Scaling":    "updating",
    "Failed":     "error",
    "Canceled":   "error",
    "Error":      "error",
    "Disabled":   "error",
    "NotFound":   "not_found",
    "Unknown":    "unknown",
}

# ── Azure Monitor metric definitions per resource type ────────────────────────
_METRICS_MAP: dict[str, list[dict]] = {
    "azurerm_postgresql_flexible_server": [
        {"name": "cpu_percent",            "unit": "%",     "label": "CPU"},
        {"name": "memory_percent",         "unit": "%",     "label": "Mémoire"},
        {"name": "storage_percent",        "unit": "%",     "label": "Stockage %"},
        {"name": "active_connections",     "unit": "count", "label": "Connexions actives"},
        {"name": "network_bytes_ingress",  "unit": "bytes", "label": "Réseau entrant"},
        {"name": "network_bytes_egress",   "unit": "bytes", "label": "Réseau sortant"},
    ],
    "azurerm_mysql_flexible_server": [
        {"name": "cpu_percent",            "unit": "%",     "label": "CPU"},
        {"name": "memory_percent",         "unit": "%",     "label": "Mémoire"},
        {"name": "active_connections",     "unit": "count", "label": "Connexions"},
        {"name": "storage_percent",        "unit": "%",     "label": "Stockage %"},
    ],
    "azurerm_storage_account": [
        {"name": "UsedCapacity",           "unit": "bytes", "label": "Capacité utilisée"},
        {"name": "Transactions",           "unit": "count", "label": "Transactions"},
        {"name": "Ingress",                "unit": "bytes", "label": "Entrées"},
        {"name": "Egress",                 "unit": "bytes", "label": "Sorties"},
        {"name": "Availability",           "unit": "%",     "label": "Disponibilité"},
        {"name": "SuccessServerLatency",   "unit": "ms",    "label": "Latence serveur"},
    ],
    "azurerm_cognitive_account": [
        {"name": "TotalCalls",             "unit": "count", "label": "Appels totaux"},
        {"name": "SuccessfulCalls",        "unit": "count", "label": "Appels réussis"},
        {"name": "TotalErrors",            "unit": "count", "label": "Erreurs"},
        {"name": "TotalTokenCalls",        "unit": "count", "label": "Tokens utilisés"},
        {"name": "Latency",                "unit": "ms",    "label": "Latence (ms)"},
    ],
    "azurerm_search_service": [
        {"name": "SearchQueriesPerSecond", "unit": "count", "label": "Requêtes/s"},
        {"name": "ThrottledSearchQueriesPercentage", "unit": "%", "label": "Throttle"},
        {"name": "DocumentsProcessedCount",          "unit": "count", "label": "Docs indexés"},
    ],
    "azurerm_kubernetes_cluster": [
        {"name": "node_cpu_usage_percentage",        "unit": "%", "label": "CPU nœuds"},
        {"name": "node_memory_working_set_percentage","unit": "%", "label": "Mémoire nœuds"},
        {"name": "kube_pod_status_phase",            "unit": "count", "label": "Pods actifs"},
    ],
    "azurerm_log_analytics_workspace": [
        {"name": "Average_% Used Space",             "unit": "%", "label": "Espace disque"},
    ],
    "azurerm_container_registry": [
        {"name": "TotalPullCount",                   "unit": "count", "label": "Pull total"},
        {"name": "TotalPushCount",                   "unit": "count", "label": "Push total"},
        {"name": "StorageUsed",                      "unit": "bytes", "label": "Stockage"},
    ],
}


# ── TFState helpers ───────────────────────────────────────────────────────────

def _get_tfstate_path(migration_id: str) -> Path:
    """Return the path to terraform.tfstate for a given migration."""
    env_dir = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    if env_dir and os.path.isabs(env_dir):
        base = Path(env_dir).parent.parent
    else:
        base = Path(__file__).parent.parent / "output"
    return base / "runs" / migration_id / "terraform.tfstate"


def get_deployed_resources(migration_id: str) -> list[dict]:
    """Parse tfstate and return a flat list of managed resource dicts."""
    path = _get_tfstate_path(migration_id)
    if not path.exists():
        logger.info("get_deployed_resources: tfstate not found at %s", path)
        return []

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("get_deployed_resources: cannot parse tfstate: %s", exc)
        return []

    resources: list[dict] = []
    for r in state.get("resources", []):
        if r.get("mode") != "managed":
            continue
        rtype = r.get("type", "")
        rname = r.get("name", "")
        for inst in r.get("instances", []):
            attrs = inst.get("attributes", {})
            rid = attrs.get("id", "")
            if not rid:
                continue
            resources.append({
                "type":                rtype,
                "name":                rname,
                "id":                  rid,
                "resource_group_name": attrs.get("resource_group_name", ""),
                "location":            attrs.get("location", ""),
                "display_name":        attrs.get("name") or rname,
                "tfstate_attrs": {
                    k: attrs.get(k)
                    for k in ("name", "location", "resource_group_name",
                               "sku_name", "version", "kind", "tier",
                               "administrator_login", "account_replication_type")
                    if attrs.get(k) is not None
                },
            })
    logger.info("get_deployed_resources: %d resource(s) found in tfstate", len(resources))
    return resources


# ── Azure credential helper ───────────────────────────────────────────────────

def _make_credential(arm_env: dict):
    from azure.identity import ClientSecretCredential
    return ClientSecretCredential(
        tenant_id=arm_env.get("ARM_TENANT_ID", ""),
        client_id=arm_env.get("ARM_CLIENT_ID", ""),
        client_secret=arm_env.get("ARM_CLIENT_SECRET", ""),
    )


# ── Health check ──────────────────────────────────────────────────────────────

def check_resource_health(resources: list[dict], arm_env: dict) -> list[dict]:
    """Call Azure Resource Manager for provisioning_state of each resource.

    Returns the resource list with added fields:
      health_status       — healthy | updating | creating | deleting | error | not_found | unknown
      provisioning_state  — raw Azure string
      arm_properties      — subset of ARM response
      error               — error message if the call failed
    """
    try:
        from azure.mgmt.resource import ResourceManagementClient
    except ImportError:
        # Some broken/partial installs expose the namespace package but drop the
        # root __init__ that re-exports ResourceManagementClient. The class still
        # lives under .resources — fall back to it before giving up.
        try:
            from azure.mgmt.resource.resources import ResourceManagementClient
        except ImportError:
            logger.warning("azure-mgmt-resource not importable — health checks skipped")
            return [
                {**r, "health_status": "unknown", "provisioning_state": "N/A",
                 "arm_properties": {}, "error": "azure-mgmt-resource not installed"}
                for r in resources
            ]

    try:
        cred = _make_credential(arm_env)
        client = ResourceManagementClient(cred, arm_env.get("ARM_SUBSCRIPTION_ID", ""))
    except Exception as exc:
        logger.error("check_resource_health: ARM client init failed: %s", exc)
        return [
            {**r, "health_status": "error", "provisioning_state": "Error",
             "arm_properties": {}, "error": f"ARM auth failed: {str(exc)[:200]}"}
            for r in resources
        ]

    results: list[dict] = []
    for r in resources:
        rid = r.get("id", "")
        if not rid:
            results.append({**r, "health_status": "unknown", "provisioning_state": "Unknown",
                             "arm_properties": {}, "error": None})
            continue

        rtype = r.get("type", "")
        api_ver = _ARM_API_VERSIONS.get(rtype, _DEFAULT_API_VERSION)

        # Some Azure resource types never expose provisioningState via ARM GET —
        # infer healthy from the fact that the resource exists (GET succeeds).
        _EXISTENCE_ONLY_TYPES = {
            "azurerm_storage_container",
            "azurerm_user_assigned_identity",
            "azurerm_private_dns_zone_virtual_network_link",
            "azurerm_subnet",
        }

        try:
            arm_res = client.resources.get_by_id(rid, api_version=api_ver)
            props = arm_res.properties or {}
            state_val = props.get("state")
            state_code = state_val.get("code") if isinstance(state_val, dict) else state_val
            prov = props.get("provisioningState") or state_code or None

            if prov is None:
                # Resource exists but has no provisioningState field
                if rtype in _EXISTENCE_ONLY_TYPES:
                    prov, health = "Succeeded", "healthy"
                else:
                    prov, health = "Unknown", "unknown"
            else:
                health = _STATE_MAP.get(prov, "unknown")
            raw_tags = getattr(arm_res, "tags", None) or {}
            tags = raw_tags if isinstance(raw_tags, dict) else {}
            results.append({
                **r,
                "health_status":     health,
                "provisioning_state": prov,
                "arm_properties": {
                    "provisioningState": prov,
                    "location":   getattr(arm_res, "location", None),
                    "kind":       getattr(arm_res, "kind", None),
                    "sku":        str(getattr(arm_res, "sku", "") or ""),
                    "tags":       tags,
                },
                "error": None,
            })
        except Exception as exc:
            err_str = str(exc)
            prov = "NotFound" if ("ResourceNotFound" in err_str or "404" in err_str) else "Error"
            results.append({
                **r,
                "health_status":     _STATE_MAP.get(prov, "error"),
                "provisioning_state": prov,
                "arm_properties":    {},
                "error":             err_str[:300],
            })
    return results


# ── Azure Monitor metrics ─────────────────────────────────────────────────────

def get_metrics(
    resources: list[dict],
    arm_env: dict,
    timespan_minutes: int = 60,
) -> dict[str, list[dict]]:
    """Fetch Azure Monitor metrics for resources that have definitions in _METRICS_MAP.

    Returns a dict keyed by "<type>.<name>" with a list of metric objects:
      {"name": str, "label": str, "unit": str, "values": [...], "latest": float | None}
    """
    try:
        from azure.mgmt.monitor import MonitorManagementClient
        from datetime import datetime, timedelta, timezone
    except ImportError:
        logger.warning("azure-mgmt-monitor not installed — metrics skipped")
        return {}

    try:
        cred = _make_credential(arm_env)
        monitor = MonitorManagementClient(cred, arm_env.get("ARM_SUBSCRIPTION_ID", ""))
    except Exception as exc:
        logger.warning("get_metrics: MonitorManagementClient init failed: %s", exc)
        return {}

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=timespan_minutes)
    interval = "PT5M"

    metrics_by_resource: dict[str, list[dict]] = {}

    for r in resources:
        rtype = r.get("type", "")
        defs = _METRICS_MAP.get(rtype)
        if not defs:
            continue
        rid = r.get("id", "")
        if not rid:
            continue

        names = [d["name"] for d in defs]
        label_map = {d["name"]: d["label"] for d in defs}
        unit_map  = {d["name"]: d["unit"]  for d in defs}

        try:
            resp = monitor.metrics.list(
                rid,
                timespan=f"{start.isoformat()}/{now.isoformat()}",
                interval=interval,
                metricnames=",".join(names),
                aggregation="Average",
            )
            parsed: list[dict] = []
            for metric in resp.value:
                mname = metric.name.value if metric.name else ""
                values = []
                for ts in (metric.timeseries or []):
                    for dp in (ts.data or []):
                        if dp.average is not None:
                            values.append({
                                "timestamp": dp.time_stamp.isoformat() if dp.time_stamp else None,
                                "value":     round(dp.average, 3),
                            })
                if values:
                    parsed.append({
                        "name":   mname,
                        "label":  label_map.get(mname, mname),
                        "unit":   unit_map.get(mname, ""),
                        "values": values[-12:],
                        "latest": values[-1]["value"] if values else None,
                    })
            if parsed:
                key = f"{rtype}.{r.get('name', '')}"
                metrics_by_resource[key] = parsed
        except Exception as exc:
            logger.debug("get_metrics: %s %s: %s", rtype, rid[:60], exc)

    return metrics_by_resource


# ── Drift detection ───────────────────────────────────────────────────────────

def detect_drift(resources: list[dict], health_results: list[dict]) -> list[dict]:
    """Compare tfstate attributes with ARM live state to detect configuration drift.

    Returns a list of drift records:
      {"resource_id", "resource_type", "resource_name", "drifts": [...]}
    """
    health_by_id = {r.get("id"): r for r in health_results}
    drifts: list[dict] = []

    for r in resources:
        rid = r.get("id", "")
        hr  = health_by_id.get(rid)
        if not hr or hr.get("health_status") in ("not_found", "error", "unknown"):
            continue

        arm_props    = hr.get("arm_properties", {})
        tfstate_attrs = r.get("tfstate_attrs", {})
        resource_drifts: list[dict] = []

        # Location drift
        tf_loc  = (tfstate_attrs.get("location") or "").lower().replace(" ", "")
        arm_loc = (arm_props.get("location") or "").lower().replace(" ", "")
        if tf_loc and arm_loc and tf_loc != arm_loc:
            resource_drifts.append({
                "attribute": "location",
                "expected":  tf_loc,
                "actual":    arm_loc,
                "severity":  "high",
            })

        # Kind drift (Cognitive Account, Storage Account)
        tf_kind  = (tfstate_attrs.get("kind") or "").lower()
        arm_kind = (arm_props.get("kind") or "").lower()
        if tf_kind and arm_kind and tf_kind != arm_kind:
            resource_drifts.append({
                "attribute": "kind",
                "expected":  tf_kind,
                "actual":    arm_kind,
                "severity":  "medium",
            })

        if resource_drifts:
            drifts.append({
                "resource_id":   rid,
                "resource_type": r.get("type"),
                "resource_name": r.get("display_name") or r.get("name"),
                "drifts":        resource_drifts,
            })

    return drifts
