"""
companions.py — Source unique de vérité pour les relations COMPANION du Graph RAG.

Importé par builder.py (insertion en base) et graph_rag.py (fallback statique).
Ne jamais dupliquer cette liste ailleurs.
"""

COMPANIONS: dict[str, list[str]] = {
    # ── AWS ───────────────────────────────────────────────────────────────────
    "aws_s3_bucket": [
        "aws_s3_bucket_versioning",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_s3_bucket_public_access_block",
    ],
    "aws_lambda_function": ["aws_iam_role", "aws_cloudwatch_log_group"],
    "aws_ecs_task_definition": ["aws_iam_role"],
    "aws_rds_cluster": ["aws_rds_cluster_instance"],
    "aws_eks_cluster": ["aws_iam_role", "aws_eks_node_group"],

    # ── GCP ───────────────────────────────────────────────────────────────────
    "google_storage_bucket": ["google_storage_bucket_iam_binding"],
    "google_sql_database_instance": ["google_sql_database", "google_sql_user"],
    "google_container_cluster": ["google_service_account", "google_container_node_pool"],
    "google_cloud_run_v2_service": ["google_service_account", "google_project_iam_member"],

    # ── Azure ─────────────────────────────────────────────────────────────────
    "azurerm_storage_account": ["azurerm_storage_container"],
    "azurerm_linux_web_app": ["azurerm_service_plan"],
    "azurerm_windows_web_app": ["azurerm_service_plan"],
    "azurerm_linux_function_app": ["azurerm_service_plan", "azurerm_storage_account"],
    "azurerm_windows_function_app": ["azurerm_service_plan", "azurerm_storage_account"],
    "azurerm_app_service": ["azurerm_app_service_plan"],
    "azurerm_sql_server": ["azurerm_sql_database", "azurerm_sql_firewall_rule"],
    "azurerm_kubernetes_cluster": ["azurerm_resource_group"],
    "azurerm_key_vault": ["azurerm_key_vault_access_policy"],
}
