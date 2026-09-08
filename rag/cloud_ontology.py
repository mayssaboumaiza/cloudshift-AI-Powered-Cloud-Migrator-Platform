"""
cloud_ontology.py — Cloud service equivalence ontology backed by PostgreSQL.

The ontology is a curated graph of (source_provider, source_service) →
(target_provider, target_service) edges with equivalence_score, compatibility_score,
confidence, and migration_type.

Used by the hybrid scoring system in scoring_decision_tools.py:
    final_score = 0.5 * embedding + 0.3 * ontology + 0.2 * schema_similarity

Public API:
    seed_ontology(force=False) → int   (upsert curated entries, returns count)
    get_ontology_score(source_provider, source_service,
                       target_provider, target_service) → float | None
    get_best_target(source_provider, source_service,
                    target_provider) → dict | None
"""
from __future__ import annotations

import logging
from typing import Any

from rag.rag_config import get_pg_connection

logger = logging.getLogger("CloudOntology")

# ── Curated ontology entries ──────────────────────────────────────────────────
# Format: (source_provider, source_service, target_provider, target_service,
#           equivalence_score, compatibility_score, confidence, migration_type, notes)
_ONTOLOGY_ENTRIES: list[tuple] = [
    # ── AWS → Azure — databases ───────────────────────────────────────────────
    ("aws", "aws_rds_cluster",            "azure", "azurerm_postgresql_flexible_server", 0.93, 0.90, 1.0, "REPLATFORM", "Aurora PostgreSQL → Azure PostgreSQL Flexible Server"),
    ("aws", "aws_rds_cluster",            "azure", "azurerm_mysql_flexible_server",      0.88, 0.85, 1.0, "REPLATFORM", "Aurora MySQL → Azure MySQL Flexible Server"),
    ("aws", "aws_db_instance",            "azure", "azurerm_postgresql_flexible_server", 0.92, 0.88, 1.0, "REPLATFORM", "RDS PostgreSQL → Azure PostgreSQL Flexible Server"),
    ("aws", "aws_db_instance",            "azure", "azurerm_mysql_flexible_server",      0.90, 0.86, 1.0, "REPLATFORM", "RDS MySQL → Azure MySQL Flexible Server"),
    ("aws", "aws_db_instance",            "azure", "azurerm_mssql_database",             0.95, 0.93, 1.0, "REPLATFORM", "RDS SQL Server → Azure SQL Database"),
    ("aws", "aws_dynamodb_table",         "azure", "azurerm_cosmosdb_account",           0.82, 0.78, 1.0, "REFACTOR",   "DynamoDB → CosmosDB (key-value/document API)"),
    ("aws", "aws_neptune_cluster",        "azure", "azurerm_cosmosdb_account",           0.79, 0.74, 0.8, "REFACTOR",   "Neptune → CosmosDB Gremlin API (graph database)"),
    # ── AWS → Azure — storage ─────────────────────────────────────────────────
    ("aws", "aws_s3_bucket",              "azure", "azurerm_storage_account",            0.91, 0.89, 1.0, "REPLATFORM", "S3 → Azure Blob Storage (SDK change required)"),
    # ── AWS → Azure — compute ─────────────────────────────────────────────────
    ("aws", "aws_instance",               "azure", "azurerm_linux_virtual_machine",      0.97, 0.96, 1.0, "REHOST",     "EC2 Linux → Azure Linux VM (reconfiguration only)"),
    ("aws", "aws_launch_template",        "azure", "azurerm_linux_virtual_machine",      0.94, 0.91, 0.9, "REHOST",     "Launch Template → Azure VM (script adaptation)"),
    # ── AWS → Azure — serverless ──────────────────────────────────────────────
    ("aws", "aws_lambda_function",        "azure", "azurerm_linux_function_app",         0.87, 0.83, 1.0, "REPLATFORM", "Lambda → Azure Functions (SDK + triggers change)"),
    # ── AWS → Azure — containers ──────────────────────────────────────────────
    ("aws", "aws_eks_cluster",            "azure", "azurerm_kubernetes_cluster",         0.96, 0.94, 1.0, "REHOST",     "EKS → AKS (kubectl/manifests compatible)"),
    ("aws", "aws_ecs_cluster",            "azure", "azurerm_container_app",              0.83, 0.79, 0.9, "REPLATFORM", "ECS Fargate → Azure Container Apps"),
    ("aws", "aws_ecr_repository",         "azure", "azurerm_container_registry",         0.96, 0.95, 1.0, "REHOST",     "ECR → ACR (docker push/pull compatible)"),
    # ── AWS → Azure — messaging ───────────────────────────────────────────────
    ("aws", "aws_sqs_queue",              "azure", "azurerm_servicebus_queue",           0.88, 0.85, 1.0, "REPLATFORM", "SQS → Service Bus Queue (API compatible, SDK change)"),
    ("aws", "aws_sns_topic",              "azure", "azurerm_servicebus_topic",           0.85, 0.82, 1.0, "REPLATFORM", "SNS → Service Bus Topic"),
    ("aws", "aws_sns_topic",              "azure", "azurerm_eventgrid_topic",            0.82, 0.78, 0.8, "REPLATFORM", "SNS → Event Grid (push events)"),
    ("aws", "aws_kinesis_stream",         "azure", "azurerm_eventhub_namespace",         0.87, 0.83, 1.0, "REPLATFORM", "Kinesis → Event Hubs (partitioned streaming)"),
    ("aws", "aws_msk_cluster",            "azure", "azurerm_eventhub_namespace",         0.85, 0.81, 0.9, "REPLATFORM", "MSK Kafka → Event Hubs Kafka protocol"),
    # ── AWS → Azure — data / analytics ────────────────────────────────────────
    ("aws", "aws_redshift_cluster",       "azure", "azurerm_synapse_workspace",          0.88, 0.84, 1.0, "REPLATFORM", "Redshift → Synapse Analytics dedicated pool"),
    ("aws", "aws_athena_workgroup",       "azure", "azurerm_synapse_workspace",          0.83, 0.79, 0.9, "REPLATFORM", "Athena → Synapse Serverless SQL pool"),
    ("aws", "aws_glue_job",               "azure", "azurerm_data_factory",               0.84, 0.80, 1.0, "REPLATFORM", "Glue ETL → Azure Data Factory pipeline"),
    ("aws", "aws_emr_cluster",            "azure", "azurerm_databricks_workspace",       0.82, 0.78, 0.9, "REPLATFORM", "EMR Spark → Azure Databricks"),
    ("aws", "aws_opensearch_domain",      "azure", "azurerm_search_service",             0.83, 0.79, 1.0, "REPLATFORM", "OpenSearch → Azure AI Search"),
    # ── AWS → Azure — ML ──────────────────────────────────────────────────────
    ("aws", "aws_sagemaker_endpoint",     "azure", "azurerm_machine_learning_workspace", 0.78, 0.72, 0.8, "REFACTOR",   "SageMaker → Azure ML (inference endpoint)"),
    # ── AWS → Azure — identity ────────────────────────────────────────────────
    ("aws", "aws_iam_role",               "azure", "azurerm_user_assigned_identity",     0.80, 0.75, 1.0, "REFACTOR",   "IAM Role → Managed Identity (model differs)"),
    ("aws", "aws_iam_policy",             "azure", "azurerm_role_definition",            0.79, 0.73, 1.0, "REFACTOR",   "IAM Policy → RBAC Role Definition"),
    ("aws", "aws_cognito_user_pool",      "azure", "azurerm_aadb2c_directory",           0.76, 0.70, 0.9, "REFACTOR",   "Cognito → AAD B2C (OIDC compatible, config rewrite)"),
    # ── AWS → Azure — networking ──────────────────────────────────────────────
    ("aws", "aws_vpc",                    "azure", "azurerm_virtual_network",            0.97, 0.96, 1.0, "REHOST",     "VPC → VNet (CIDR compatible)"),
    ("aws", "aws_subnet",                 "azure", "azurerm_subnet",                     0.97, 0.96, 1.0, "REHOST",     "Subnet → Subnet"),
    ("aws", "aws_security_group",         "azure", "azurerm_network_security_group",     0.96, 0.94, 1.0, "REHOST",     "SG → NSG (rule semantics identical)"),
    ("aws", "aws_route53_zone",           "azure", "azurerm_dns_zone",                   0.96, 0.95, 1.0, "REHOST",     "Route53 → Azure DNS"),
    ("aws", "aws_cloudfront_distribution","azure", "azurerm_cdn_profile",                0.88, 0.84, 1.0, "REPLATFORM", "CloudFront → Azure CDN / Front Door"),
    ("aws", "aws_api_gateway_rest_api",   "azure", "azurerm_api_management",             0.82, 0.78, 1.0, "REPLATFORM", "API Gateway → APIM (policy language differs)"),
    ("aws", "aws_wafv2_web_acl",          "azure", "azurerm_frontdoor_profile",          0.83, 0.79, 0.9, "REPLATFORM", "WAFv2 → Azure Front Door WAF"),
    # ── AWS → Azure — secrets / monitoring ────────────────────────────────────
    ("aws", "aws_kms_key",                "azure", "azurerm_key_vault_key",              0.94, 0.92, 1.0, "REPLATFORM", "KMS → Key Vault key (SDK change)"),
    ("aws", "aws_secretsmanager_secret",  "azure", "azurerm_key_vault_secret",           0.93, 0.91, 1.0, "REPLATFORM", "Secrets Manager → Key Vault secret"),
    ("aws", "aws_cloudwatch_metric_alarm","azure", "azurerm_monitor_metric_alert",       0.92, 0.90, 1.0, "REPLATFORM", "CloudWatch Alarm → Azure Monitor Alert"),
    ("aws", "aws_cloudwatch_log_group",   "azure", "azurerm_log_analytics_workspace",    0.90, 0.87, 1.0, "REPLATFORM", "CloudWatch Logs → Log Analytics"),
    # ── AWS → Azure — workflow ────────────────────────────────────────────────
    ("aws", "aws_step_functions_state_machine", "azure", "azurerm_logic_app_workflow",   0.79, 0.74, 0.9, "REFACTOR",   "Step Functions → Logic Apps (JSON workflow rewrite)"),
    # ── AWS → GCP — spot mappings ─────────────────────────────────────────────
    ("aws", "aws_s3_bucket",              "gcp",   "google_storage_bucket",              0.93, 0.91, 1.0, "REPLATFORM", "S3 → GCS"),
    ("aws", "aws_lambda_function",        "gcp",   "google_cloudfunctions_function",     0.86, 0.82, 1.0, "REPLATFORM", "Lambda → Cloud Functions"),
    ("aws", "aws_eks_cluster",            "gcp",   "google_container_cluster",           0.96, 0.94, 1.0, "REHOST",     "EKS → GKE"),
    ("aws", "aws_rds_cluster",            "gcp",   "google_sql_database_instance",       0.90, 0.87, 1.0, "REPLATFORM", "RDS → Cloud SQL"),
    ("aws", "aws_dynamodb_table",         "gcp",   "google_firestore_database",          0.80, 0.75, 0.9, "REFACTOR",   "DynamoDB → Firestore"),
    ("aws", "aws_redshift_cluster",       "gcp",   "google_bigquery_dataset",            0.87, 0.83, 1.0, "REPLATFORM", "Redshift → BigQuery"),
]


def seed_ontology(force: bool = False) -> int:
    """Upsert curated ontology entries into cloud_service_ontology.

    If force=False, skips entries that already exist (by unique index).
    Returns the number of rows upserted.
    """
    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            upserted = 0
            for entry in _ONTOLOGY_ENTRIES:
                (src_prov, src_svc, tgt_prov, tgt_svc,
                 eq, compat, conf, mtype, notes) = entry
                if force:
                    cur.execute("""
                        INSERT INTO cloud_service_ontology
                          (source_provider, source_service, target_provider, target_service,
                           equivalence_score, compatibility_score, confidence, migration_type, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT ON CONSTRAINT ix_ontology_pair
                        DO UPDATE SET
                          equivalence_score   = EXCLUDED.equivalence_score,
                          compatibility_score = EXCLUDED.compatibility_score,
                          confidence          = EXCLUDED.confidence,
                          migration_type      = EXCLUDED.migration_type,
                          notes               = EXCLUDED.notes,
                          last_updated        = NOW()
                    """, (src_prov, src_svc, tgt_prov, tgt_svc, eq, compat, conf, mtype, notes))
                else:
                    cur.execute("""
                        INSERT INTO cloud_service_ontology
                          (source_provider, source_service, target_provider, target_service,
                           equivalence_score, compatibility_score, confidence, migration_type, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (src_prov, src_svc, tgt_prov, tgt_svc, eq, compat, conf, mtype, notes))
                upserted += cur.rowcount
            conn.commit()
            logger.info("Cloud ontology seeded: %d entries upserted", upserted)
            return upserted
    except Exception as exc:
        logger.error("seed_ontology failed: %s", exc)
        return 0


def get_ontology_score(
    source_provider: str,
    source_service: str,
    target_provider: str,
    target_service: str,
) -> float | None:
    """Return the equivalence_score from the ontology for this pair, or None if not found."""
    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT equivalence_score, confidence
                FROM cloud_service_ontology
                WHERE source_provider = %s
                  AND source_service   = %s
                  AND target_provider  = %s
                  AND target_service   = %s
                LIMIT 1
            """, (source_provider.lower(), source_service.lower(),
                  target_provider.lower(), target_service.lower()))
            row = cur.fetchone()
            if row:
                eq_score, confidence = row
                return float(eq_score) * float(confidence)
            return None
    except Exception as exc:
        logger.debug("get_ontology_score failed: %s", exc)
        return None


def absorb_dynamic_tag(
    source_provider: str,
    source_service: str,
    target_provider: str,
    tag_text: str,
    confidence: float = 0.5,
) -> bool:
    """Absorb a dynamically generated tag into the ontology as a low-confidence entry.

    Called by dynamic_tag_enricher after LLM tag generation so that future
    migrations benefit from accumulated equivalences. Entries created here
    have confidence=0.5 (vs 1.0 for curated entries) and migration_type=UNKNOWN.
    They are never overwritten by seed_ontology (ON CONFLICT DO NOTHING).

    Returns True if a new entry was inserted, False if already existed or failed.
    """
    # Extract target_service from tag_text: look for known Terraform resource prefixes
    import re
    target_service = None
    for prefix in ("azurerm_", "google_", "aws_"):
        m = re.search(rf"\b({prefix}\w+)\b", tag_text)
        if m:
            target_service = m.group(1)
            break

    if not target_service:
        return False

    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO cloud_service_ontology
                  (source_provider, source_service, target_provider, target_service,
                   equivalence_score, compatibility_score, confidence, migration_type, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT ix_ontology_pair DO NOTHING
            """, (
                source_provider.lower(), source_service.lower(),
                target_provider.lower(), target_service.lower(),
                0.60, 0.55, confidence, "UNKNOWN",
                f"auto-generated from LLM tag: {tag_text[:120]}",
            ))
            inserted = cur.rowcount > 0
            conn.commit()
            if inserted:
                logger.info(
                    "CloudOntology: absorbed dynamic tag '%s' → '%s' (confidence=%.2f)",
                    source_service, target_service, confidence,
                )
            return inserted
    except Exception as exc:
        logger.debug("absorb_dynamic_tag(%s): %s", source_service, exc)
        return False


def get_best_target(
    source_provider: str,
    source_service: str,
    target_provider: str,
) -> dict[str, Any] | None:
    """Return the best ontology match for this source→target_provider pair."""
    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT target_service, equivalence_score, compatibility_score,
                       confidence, migration_type, notes
                FROM cloud_service_ontology
                WHERE source_provider = %s
                  AND source_service   = %s
                  AND target_provider  = %s
                ORDER BY equivalence_score * confidence DESC
                LIMIT 1
            """, (source_provider.lower(), source_service.lower(), target_provider.lower()))
            row = cur.fetchone()
            if row:
                return {
                    "target_service":      row[0],
                    "equivalence_score":   row[1],
                    "compatibility_score": row[2],
                    "confidence":          row[3],
                    "migration_type":      row[4],
                    "notes":               row[5],
                }
            return None
    except Exception as exc:
        logger.debug("get_best_target failed: %s", exc)
        return None
