"""
intent_validator.py — Semantic intent validation for migration planning.

Detects mismatches between the detected source service intent (boto3 SDK calls,
Terraform resource types) and the proposed Azure/GCP target resource.

Examples of mismatches this module catches:
  - boto3.client("rds")  → azurerm_cosmosdb_account      (relational → NoSQL)
  - boto3.client("s3")   → azurerm_postgresql_flexible_server (storage → DB)
  - aws_redshift_cluster → azurerm_linux_function_app     (warehouse → function)

Usage:
    from agents.migration_planner.intent_validator import validate_migration_intent
    issues = validate_migration_intent(migration_plan)
    # returns list[str] of human-readable mismatch descriptions
"""
from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger("IntentValidator")

# ── Service category taxonomy ─────────────────────────────────────────────────
# Each resource is mapped to its functional category.
# Used to detect cross-category mismatches (relational→nosql, storage→db, etc.)

_CATEGORY_MAP: dict[str, str] = {
    # AWS — relational databases
    "aws_rds_cluster":                  "database_relational",
    "aws_rds_cluster_instance":         "database_relational",
    "aws_db_instance":                  "database_relational",
    # AWS — NoSQL databases
    "aws_dynamodb_table":               "database_nosql",
    "aws_neptune_cluster":              "database_graph",
    "aws_elasticache_cluster":          "cache",
    "aws_elasticache_replication_group":"cache",
    # AWS — object storage
    "aws_s3_bucket":                    "object_storage",
    "aws_s3_object":                    "object_storage",
    # AWS — compute
    "aws_instance":                     "compute_vm",
    "aws_launch_template":              "compute_vm",
    "aws_autoscaling_group":            "compute_vm",
    # AWS — serverless
    "aws_lambda_function":              "serverless_function",
    # AWS — containers
    "aws_eks_cluster":                  "container_orchestration",
    "aws_ecs_cluster":                  "container_orchestration",
    "aws_ecs_service":                  "container_orchestration",
    "aws_ecr_repository":               "container_registry",
    # AWS — messaging
    "aws_sqs_queue":                    "message_queue",
    "aws_sns_topic":                    "message_topic",
    "aws_kinesis_stream":               "event_stream",
    "aws_msk_cluster":                  "event_stream",
    # AWS — data / analytics
    "aws_redshift_cluster":             "data_warehouse",
    "aws_athena_workgroup":             "analytics_query",
    "aws_glue_job":                     "etl_pipeline",
    "aws_emr_cluster":                  "big_data",
    "aws_opensearch_domain":            "search_engine",
    # AWS — identity
    "aws_iam_role":                     "iam_role",
    "aws_iam_policy":                   "iam_policy",
    "aws_cognito_user_pool":            "identity_auth",
    # AWS — networking
    "aws_vpc":                          "network",
    "aws_subnet":                       "network",
    "aws_security_group":               "network_security",
    "aws_route53_zone":                 "dns",
    "aws_cloudfront_distribution":      "cdn",
    "aws_api_gateway_rest_api":         "api_gateway",
    "aws_wafv2_web_acl":               "waf",
    # AWS — secrets / KMS
    "aws_kms_key":                      "encryption_key",
    "aws_secretsmanager_secret":        "secrets_manager",
    # AWS — monitoring
    "aws_cloudwatch_metric_alarm":      "monitoring_alert",
    "aws_cloudwatch_log_group":         "log_storage",
    # AWS — ML
    "aws_sagemaker_endpoint":           "ml_inference",
    # AWS — workflow
    "aws_step_functions_state_machine": "workflow_orchestration",

    # Azure — relational databases
    "azurerm_postgresql_flexible_server":   "database_relational",
    "azurerm_mysql_flexible_server":        "database_relational",
    "azurerm_mssql_server":                 "database_relational",
    "azurerm_mssql_database":               "database_relational",
    "azurerm_sql_server":                   "database_relational",
    "azurerm_sql_database":                 "database_relational",
    # Azure — NoSQL
    "azurerm_cosmosdb_account":             "database_nosql",
    # Azure — cache
    "azurerm_redis_cache":                  "cache",
    # Azure — object storage
    "azurerm_storage_account":             "object_storage",
    "azurerm_storage_container":           "object_storage",
    # Azure — compute
    "azurerm_linux_virtual_machine":       "compute_vm",
    "azurerm_windows_virtual_machine":     "compute_vm",
    "azurerm_virtual_machine_scale_set":   "compute_vm",
    # Azure — serverless
    "azurerm_linux_function_app":          "serverless_function",
    "azurerm_function_app":                "serverless_function",
    # Azure — containers
    "azurerm_kubernetes_cluster":          "container_orchestration",
    "azurerm_container_app":               "container_orchestration",
    "azurerm_container_registry":          "container_registry",
    # Azure — messaging
    "azurerm_servicebus_queue":            "message_queue",
    "azurerm_servicebus_topic":            "message_topic",
    "azurerm_eventhub_namespace":          "event_stream",
    "azurerm_eventhub":                    "event_stream",
    # Azure — data / analytics
    "azurerm_synapse_workspace":           "data_warehouse",
    "azurerm_synapse_sql_pool":            "data_warehouse",
    "azurerm_data_factory":                "etl_pipeline",
    "azurerm_databricks_workspace":        "big_data",
    "azurerm_search_service":              "search_engine",
    # Azure — identity
    "azurerm_user_assigned_identity":      "iam_role",
    "azurerm_role_definition":             "iam_policy",
    "azurerm_role_assignment":             "iam_policy",
    "azurerm_aadb2c_directory":            "identity_auth",
    # Azure — networking
    "azurerm_virtual_network":             "network",
    "azurerm_subnet":                      "network",
    "azurerm_network_security_group":      "network_security",
    "azurerm_dns_zone":                    "dns",
    "azurerm_cdn_profile":                 "cdn",
    "azurerm_frontdoor_profile":           "cdn",
    "azurerm_api_management":              "api_gateway",
    # Azure — secrets / KMS
    "azurerm_key_vault_key":               "encryption_key",
    "azurerm_key_vault_secret":            "secrets_manager",
    "azurerm_key_vault":                   "secrets_manager",
    # Azure — monitoring
    "azurerm_monitor_metric_alert":        "monitoring_alert",
    "azurerm_log_analytics_workspace":     "log_storage",
    # Azure — ML / workflow
    "azurerm_machine_learning_workspace":  "ml_inference",
    "azurerm_logic_app_workflow":          "workflow_orchestration",
}

# ── boto3 SDK client name → functional category ───────────────────────────────
_SDK_CATEGORY_MAP: dict[str, str] = {
    "s3":              "object_storage",
    "rds":             "database_relational",
    "dynamodb":        "database_nosql",
    "neptune":         "database_graph",
    "elasticache":     "cache",
    "lambda":          "serverless_function",
    "ec2":             "compute_vm",
    "eks":             "container_orchestration",
    "ecs":             "container_orchestration",
    "ecr":             "container_registry",
    "sqs":             "message_queue",
    "sns":             "message_topic",
    "kinesis":         "event_stream",
    "kafka":           "event_stream",
    "redshift":        "data_warehouse",
    "athena":          "analytics_query",
    "glue":            "etl_pipeline",
    "emr":             "big_data",
    "opensearch":      "search_engine",
    "es":              "search_engine",
    "iam":             "iam_role",
    "cognito":         "identity_auth",
    "route53":         "dns",
    "cloudfront":      "cdn",
    "apigateway":      "api_gateway",
    "kms":             "encryption_key",
    "secretsmanager":  "secrets_manager",
    "cloudwatch":      "monitoring_alert",
    "logs":            "log_storage",
    "sagemaker":       "ml_inference",
    "stepfunctions":   "workflow_orchestration",
    "msk":             "event_stream",
    "wafv2":           "waf",
}

# ── Compatible category groups (source → allowed target categories) ───────────
# If a mismatch is outside this map, it is flagged as a planning error.
_COMPATIBLE_TARGETS: dict[str, set[str]] = {
    "database_relational":   {"database_relational"},
    "database_nosql":        {"database_nosql"},
    "database_graph":        {"database_graph", "database_nosql"},  # CosmosDB Gremlin
    "cache":                 {"cache"},
    "object_storage":        {"object_storage"},
    "compute_vm":            {"compute_vm", "container_orchestration"},
    "serverless_function":   {"serverless_function", "container_orchestration"},
    "container_orchestration": {"container_orchestration", "serverless_function"},
    "container_registry":    {"container_registry"},
    "message_queue":         {"message_queue", "message_topic"},
    "message_topic":         {"message_topic", "message_queue"},
    "event_stream":          {"event_stream", "message_queue"},
    "data_warehouse":        {"data_warehouse", "analytics_query"},
    "analytics_query":       {"analytics_query", "data_warehouse"},
    "etl_pipeline":          {"etl_pipeline"},
    "big_data":              {"big_data", "etl_pipeline"},
    "search_engine":         {"search_engine"},
    "iam_role":              {"iam_role", "iam_policy"},
    "iam_policy":            {"iam_policy", "iam_role"},
    "identity_auth":         {"identity_auth"},
    "network":               {"network"},
    "network_security":      {"network_security"},
    "dns":                   {"dns"},
    "cdn":                   {"cdn"},
    "api_gateway":           {"api_gateway"},
    "waf":                   {"waf", "cdn"},
    "encryption_key":        {"encryption_key", "secrets_manager"},
    "secrets_manager":       {"secrets_manager", "encryption_key"},
    "monitoring_alert":      {"monitoring_alert", "log_storage"},
    "log_storage":           {"log_storage", "monitoring_alert"},
    "ml_inference":          {"ml_inference"},
    "workflow_orchestration": {"workflow_orchestration"},
}


class IntentMismatch(NamedTuple):
    service_name: str
    source_service: str
    target_service: str
    source_category: str
    target_category: str
    severity: str       # "ERROR" | "WARNING"
    message: str


def _get_category(resource_type: str) -> str | None:
    """Return the functional category for a resource type, or None if unknown."""
    return _CATEGORY_MAP.get(resource_type.lower().strip())


def _get_sdk_category(sdk_client: str) -> str | None:
    """Return the functional category for a boto3 client name."""
    return _SDK_CATEGORY_MAP.get(sdk_client.lower().strip())


def validate_migration_intent(migration_plan: dict) -> list[IntentMismatch]:
    """Check each planned migration for source↔target category mismatches.

    Iterates over migration_plan["services"] (or ["resources"]).
    For each service: derives source category and target category, then checks
    whether the target is in the set of compatible targets for the source.

    Returns a list of IntentMismatch named tuples.
    Empty list = no issues detected.
    """
    mismatches: list[IntentMismatch] = []
    services = (migration_plan or {}).get("services") or migration_plan.get("resources") or []

    for svc in services:
        service_name  = svc.get("service_name") or svc.get("resource_name") or "unknown"
        source_svc    = (svc.get("source_service") or "").strip()
        target_svc    = (svc.get("target_service") or svc.get("resource_name") or "").strip()
        strategy      = (svc.get("strategy_7r") or svc.get("strategy") or "").upper()
        sdk_calls     = svc.get("sdk_calls") or []  # list of boto3 client names

        if strategy in ("RETIRE", "RETAIN"):
            continue  # no target resource — nothing to validate

        # Derive source category
        src_cat = _get_category(source_svc)

        # If source category unknown, try to infer from SDK calls
        if src_cat is None and sdk_calls:
            for call in (sdk_calls if isinstance(sdk_calls, list) else [sdk_calls]):
                client_name = str(call).replace("boto3.client(", "").strip("()\"' ")
                src_cat = _get_sdk_category(client_name)
                if src_cat:
                    break

        if src_cat is None:
            logger.debug(
                "[IntentValidator] %s: source category unknown for '%s' — skipping",
                service_name, source_svc,
            )
            continue

        tgt_cat = _get_category(target_svc)
        if tgt_cat is None:
            logger.debug(
                "[IntentValidator] %s: target category unknown for '%s' — skipping",
                service_name, target_svc,
            )
            continue

        allowed = _COMPATIBLE_TARGETS.get(src_cat, {src_cat})
        if tgt_cat not in allowed:
            severity = "ERROR" if strategy == "REFACTOR" else "WARNING"
            msg = (
                f"Intent mismatch for '{service_name}': "
                f"source '{source_svc}' is category '{src_cat}' "
                f"but target '{target_svc}' is category '{tgt_cat}'. "
                f"Expected one of: {sorted(allowed)}. "
                f"This may indicate a planning error — verify the 7R strategy."
            )
            logger.warning("[IntentValidator] %s", msg)
            mismatches.append(IntentMismatch(
                service_name=service_name,
                source_service=source_svc,
                target_service=target_svc,
                source_category=src_cat,
                target_category=tgt_cat,
                severity=severity,
                message=msg,
            ))
        else:
            logger.debug(
                "[IntentValidator] %s: ✓ %s → %s (categories: %s → %s)",
                service_name, source_svc, target_svc, src_cat, tgt_cat,
            )

    return mismatches


def mismatches_to_issues(mismatches: list[IntentMismatch]) -> list[str]:
    """Convert mismatch list to plain string list for pipeline state."""
    return [m.message for m in mismatches]
