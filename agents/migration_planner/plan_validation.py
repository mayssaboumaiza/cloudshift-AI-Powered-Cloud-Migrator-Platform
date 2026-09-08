"""
plan_validation.py — Agent 01 plan validation, normalization, and anti-hallucination guards.

Responsibilities:
  - Validate the structure and field values of a migration plan dict (v2 format).
  - Normalize each service item to the shape expected by Agent 02, Agent 03,
    and the frontend (adds compat fields, derives complexity, builds sdk_changes).
  - Guard against LLM hallucinations by checking Terraform resource type prefixes
    against a built-in whitelist of known resource types per provider.

Exported symbols used by planner.py and plan_execution.py:
  _VALID_STRATEGIES       — set of accepted strategy_7r values
  _validate_plan_inline() — structural validation, returns list of error strings
  _normalize_service()    — enrich a single service dict for downstream consumers
  _normalize_resources()  — apply _normalize_service() to a list
  _validate_tf_resource_type() — check a resource type string against the whitelist
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("Agent01")


# ─────────────────────────────────────────────────────────────────────────────
# Valid 7R strategy values
# ─────────────────────────────────────────────────────────────────────────────

_VALID_STRATEGIES = {
    "REHOST", "REPLATFORM", "REFACTOR", "REPURCHASE",
    "RETIRE", "RETAIN", "RELOCATE",
}


# ─────────────────────────────────────────────────────────────────────────────
# Plan structural validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_plan_inline(plan: dict) -> list[str]:
    """Validate a v2 migration plan (key 'services').

    Returns:
        List of error messages — empty list means the plan is valid.
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return ["Plan must be a dictionary"]

    if "services" not in plan:
        return ["Missing required key: 'services'"]

    services = plan["services"]
    if not isinstance(services, list):
        return ["'services' must be a list"]

    required = ["service_name", "source_service", "target_service", "strategy_7r"]

    for idx, svc in enumerate(services):
        if not isinstance(svc, dict):
            errors.append(f"services[{idx}] must be a dictionary")
            continue
        for field in required:
            if field not in svc:
                errors.append(f"services[{idx}] missing '{field}'")
            elif field == "target_service" and not str(svc.get(field, "")).strip():
                if svc.get("strategy_7r", "").upper() not in {"RETAIN", "RETIRE"}:
                    errors.append(
                        f"services[{idx}] 'target_service' is empty — "
                        "must specify a valid Terraform resource type (e.g. 'azurerm_postgresql_flexible_server')"
                    )
        strategy = svc.get("strategy_7r", "")
        if strategy.upper() not in _VALID_STRATEGIES:
            errors.append(
                f"services[{idx}] invalid strategy_7r '{strategy}'. "
                f"Must be one of: {', '.join(sorted(_VALID_STRATEGIES))}"
            )

        # Anti-hallucination: verify target_service has the correct provider prefix.
        # The cloud is inferred from the prefix itself so no extra argument is needed.
        target_svc = str(svc.get("target_service", "")).strip()
        if target_svc:
            inferred_cloud = (
                "azure" if target_svc.startswith("azurerm_") else
                "gcp"   if target_svc.startswith("google_")  else
                "aws"   if target_svc.startswith("aws_")     else
                ""
            )
            if inferred_cloud:
                _, is_valid = _validate_tf_resource_type(target_svc, inferred_cloud)
                if not is_valid:
                    errors.append(
                        f"services[{idx}] 'target_service' '{target_svc}' has wrong "
                        f"provider prefix for '{inferred_cloud}' — hallucination detected. "
                        f"Use the correct azurerm_/google_/aws_ prefixed resource type."
                    )

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Anti-hallucination whitelist — known Terraform resource types per provider
# ─────────────────────────────────────────────────────────────────────────────

_TF_WHITELIST: dict[str, set[str]] = {
    "azure": {
        "azurerm_storage_account", "azurerm_storage_container", "azurerm_storage_blob",
        "azurerm_linux_function_app", "azurerm_windows_function_app", "azurerm_function_app",
        "azurerm_service_plan", "azurerm_app_service_plan",
        "azurerm_postgresql_flexible_server", "azurerm_postgresql_server",
        "azurerm_cosmosdb_account", "azurerm_cosmosdb_sql_database",
        "azurerm_redis_cache", "azurerm_servicebus_namespace", "azurerm_servicebus_queue",
        "azurerm_kubernetes_cluster", "azurerm_container_app", "azurerm_container_app_environment",
        "azurerm_container_registry", "azurerm_linux_web_app", "azurerm_windows_web_app",
        "azurerm_virtual_machine", "azurerm_virtual_network", "azurerm_subnet",
        "azurerm_network_security_group", "azurerm_public_ip", "azurerm_load_balancer",
        "azurerm_monitor_metric_alert", "azurerm_log_analytics_workspace",
        "azurerm_application_insights", "azurerm_key_vault", "azurerm_key_vault_secret",
        "azurerm_user_assigned_identity", "azurerm_role_assignment",
        "azurerm_eventhub_namespace", "azurerm_eventhub",
        "azurerm_search_service", "azurerm_cognitive_account",
        "azurerm_mysql_flexible_server", "azurerm_mssql_server", "azurerm_mssql_database",
    },
    "gcp": {
        "google_storage_bucket", "google_storage_bucket_object",
        "google_cloudfunctions2_function", "google_cloudfunctions_function",
        "google_cloud_run_service", "google_cloud_run_v2_service",
        "google_sql_database_instance", "google_sql_database", "google_sql_user",
        "google_firestore_document", "google_firestore_index",
        "google_bigtable_instance", "google_spanner_instance",
        "google_pubsub_topic", "google_pubsub_subscription",
        "google_container_cluster", "google_container_node_pool",
        "google_compute_instance", "google_compute_network", "google_compute_subnetwork",
        "google_compute_firewall", "google_compute_forwarding_rule",
        "google_monitoring_alert_policy", "google_logging_metric",
        "google_service_account", "google_project_iam_binding", "google_project_iam_member",
        "google_redis_instance", "google_memcache_instance",
        "google_artifact_registry_repository", "google_container_registry",
        "google_vertex_ai_endpoint", "google_bigquery_dataset", "google_bigquery_table",
    },
    "aws": {
        "aws_s3_bucket", "aws_s3_bucket_policy", "aws_s3_bucket_acl",
        "aws_lambda_function", "aws_lambda_event_source_mapping",
        "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance",
        "aws_dynamodb_table", "aws_elasticache_cluster", "aws_elasticache_replication_group",
        "aws_ecs_cluster", "aws_ecs_service", "aws_ecs_task_definition",
        "aws_eks_cluster", "aws_eks_node_group",
        "aws_sqs_queue", "aws_sns_topic", "aws_sns_subscription",
        "aws_instance", "aws_vpc", "aws_subnet", "aws_security_group",
        "aws_cloudwatch_metric_alarm", "aws_cloudwatch_log_group",
        "aws_iam_role", "aws_iam_policy", "aws_iam_role_policy_attachment",
        "aws_ecr_repository", "aws_apprunner_service",
        "aws_apigatewayv2_api", "aws_api_gateway_rest_api",
        "aws_kms_key", "aws_secretsmanager_secret",
        "aws_kinesis_stream", "aws_kinesis_firehose_delivery_stream",
    },
}


def _validate_tf_resource_type(resource_type: str, target_cloud: str) -> tuple[str, bool]:
    """Check whether a Terraform resource type matches the expected provider prefix.

    Returns:
        (resource_type, is_valid) — is_valid is False when the provider prefix
        is wrong (clear hallucination); True when the type is in the whitelist
        or has the correct prefix but is unknown (newer resource type).
    """
    if not resource_type:
        return resource_type, False

    provider_prefix = {
        "azure": "azurerm_",
        "gcp":   "google_",
        "aws":   "aws_",
    }.get(target_cloud.lower(), "")

    if provider_prefix and not resource_type.startswith(provider_prefix):
        logger.warning(
            f"[Agent01] Hallucination guard: '{resource_type}' does not start with "
            f"'{provider_prefix}' for target_cloud='{target_cloud}'"
        )
        return resource_type, False

    wl = _TF_WHITELIST.get(target_cloud.lower(), set())
    if wl and resource_type not in wl:
        logger.info(
            f"[Agent01] '{resource_type}' not in built-in whitelist — "
            "may be a newer resource type, keeping but flagging"
        )
        return resource_type, True  # correct prefix, unknown name → keep

    return resource_type, True


# ─────────────────────────────────────────────────────────────────────────────
# Service normalization — enrich LLM output for Agent 02 / 03 / frontend
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_service(svc: dict, source_cloud: str, target_cloud: str) -> dict:
    """Enrich a single v2 service dict with compatibility fields.

    Agent 02 reads : strategy, sdk_changes
    Agent 03 reads : resource_name, strategy
    Frontend reads : type, score, composite_score, monthly_cost_estimate, complexity
    """
    s = svc.copy()

    # Canonical v2 fields
    service_name = s.get("service_name") or s.get("source_service") or "unknown"
    strategy_7r  = s.get("strategy_7r", "REPLATFORM").upper()
    s["service_name"] = service_name
    s["strategy_7r"]  = strategy_7r

    # Agent 02/03 compat fields
    s.setdefault("resource_name", service_name)
    s.setdefault("strategy", strategy_7r)
    s.setdefault("source_cloud", source_cloud)
    s.setdefault("target_cloud", target_cloud)

    # Hallucination guard on target_service
    ts = s.get("target_service", "")
    if ts:
        _ts_validated, ts_ok = _validate_tf_resource_type(ts, target_cloud)
        if not ts_ok:
            logger.warning(
                f"[Agent01] '{service_name}': target_service '{ts}' uses wrong provider prefix "
                f"for target_cloud='{target_cloud}' — flagged for review."
            )
            s["llm_target_service_suspect"] = ts

    s.setdefault("target_equivalent", s.get("target_service", ""))
    s.setdefault("terraform_resource", s.get("target_service", ""))

    # sdk_changes (Agent 02) — built from breaking_changes
    if "sdk_changes" not in s:
        bc   = s.get("breaking_changes", [])
        auth = s.get("auth_change", "")
        bc0  = bc[0] if bc and isinstance(bc[0], dict) else {}
        s["sdk_changes"] = {
            "breaking_changes": bc,
            "auth_change":       auth,
            "source_import":     bc0.get("pattern", ""),
            "target_import":     bc0.get("replacement", ""),
            "pip_changes":       {},
        }

    # Frontend fields
    s.setdefault("type", s.get("category", "unknown"))
    s.setdefault("cloud_confirmed", True)
    s.setdefault("code_patterns", {})

    eq = float(s.get("equivalence_score", 0.7))
    s.setdefault("composite_score", round(eq, 4))

    if "score" not in s:
        try:
            s["score"] = float(s.get("composite_score", 0))
        except (TypeError, ValueError):
            s["score"] = 0.0

    s.setdefault("weighted_total", s.get("score", round(eq * 10, 1)))

    monthly_eur = float(s.get("monthly_cost_eur", 0.0) or 0.0)
    if monthly_eur == 0.0 and s.get("target_service"):
        try:
            from agents.migration_planner.service_lookup_tools import estimate_cost
            import json as _json
            svc_norm = s["target_service"].replace("azurerm_", "").replace("google_", "").replace("_", "-")
            cost_raw = estimate_cost.invoke({
                "service": svc_norm,
                "region": "",
                "cloud_target": target_cloud,
                "instance_class": s.get("contextual_hints", {}).get("instance_class", ""),
            })
            cost_data = _json.loads(cost_raw)
            api_cost = float(cost_data.get("monthly_cost_eur") or 0.0)
            if api_cost > 0:
                monthly_eur = api_cost
                s["pricing_source"] = cost_data.get("source", target_cloud)
        except Exception:
            pass
    s.setdefault("monthly_cost_estimate", monthly_eur)
    s.setdefault("pricing_source", "llm-estimate")
    s.setdefault("best_discount", None)

    # Complexity: priority order —
    #   1. Neo4j graph signal (structural, objective)
    #   2. LLM value if valid
    #   3. Heuristic fallback (category + strategy)

    # Signal Neo4j — query graph complexity for the target terraform resource
    neo4j_complexity: str | None = None
    terraform_resource = s.get("terraform_resource") or s.get("target_service", "")
    if terraform_resource:
        try:
            from rag.graph_rag import TerraformGraphRAG
            rag = TerraformGraphRAG.get_instance()
            cx = rag.get_migration_complexity(terraform_resource)
            neo4j_complexity = cx.get("complexity_label")  # "LOW" | "MEDIUM" | "HIGH"
            s["graph_complexity"] = {
                "score":            cx.get("complexity_score"),
                "label":            neo4j_complexity,
                "dependency_depth": cx.get("dependency_depth"),
                "required_args":    cx.get("required_arg_count"),
                "companions":       cx.get("companion_count"),
                "source":           cx.get("source"),
            }
        except Exception:
            pass

    if neo4j_complexity in {"LOW", "MEDIUM", "HIGH"}:
        s["complexity"] = neo4j_complexity
    else:
        llm_complexity = str(s.get("complexity") or "").upper().strip()
        if llm_complexity in {"LOW", "MEDIUM", "HIGH"}:
            s["complexity"] = llm_complexity
        else:
            category = str(s.get("category") or s.get("type") or "").lower()
            iam_kws = {"iam", "identity", "security", "auth", "cognito", "entraid"}
            db_kws  = {"database", "db", "nosql", "sql", "relational"}
            ai_kws  = {"ai", "ml", "llm", "vector", "embedding", "inference"}
            low_kws = {"networking", "network", "subnet", "vpc", "dns", "cdn",
                       "container-registry", "registry"}
            if any(k in category for k in iam_kws | db_kws | ai_kws):
                s["complexity"] = "HIGH"
            elif strategy_7r == "REHOST" and float(s.get("equivalence_score", 0)) >= 0.90:
                s["complexity"] = "LOW"
            elif any(k in category for k in low_kws) and strategy_7r == "REHOST":
                s["complexity"] = "LOW"
            elif strategy_7r == "REFACTOR":
                s["complexity"] = "HIGH"
            elif strategy_7r == "REPLATFORM":
                s["complexity"] = "MEDIUM"
            else:
                s["complexity"] = "LOW"

    # Alerts from boolean flags
    if "alerts" not in s:
        alerts: list[dict] = []
        if s.get("budget_ok") is False:
            alerts.append({"level": "HIGH", "message": "Service cost exceeds budget"})
        if s.get("region_available") is False:
            alerts.append({"level": "HIGH", "message": "Service not available in target region"})
        if s.get("gdpr_compliant") is False:
            alerts.append({"level": "MEDIUM", "message": "GDPR compliance review required"})
        s["alerts"] = alerts

    s.setdefault("maturity_status", "GA")
    s.setdefault("preview_flag", False)
    s.setdefault("modernity_note", (s.get("reasoning") or "")[:120])

    s.setdefault("validated", {
        "region_available": s.get("region_available", True),
        "budget_fits":      s.get("budget_ok", True),
        "score":            s.get("composite_score", eq),
    })

    return s


def _normalize_resources(resources: list[dict], source_cloud: str, target_cloud: str) -> list[dict]:
    """Apply _normalize_service() to every item in a list."""
    return [_normalize_service(item, source_cloud, target_cloud)
            for item in (resources or []) if isinstance(item, dict)]
