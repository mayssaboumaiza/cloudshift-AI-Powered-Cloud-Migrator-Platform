"""Unit tests for agents/migration_planner/intent_validator.py."""
import pytest

from agents.migration_planner.intent_validator import (
    IntentMismatch,
    _get_category,
    _get_sdk_category,
    mismatches_to_issues,
    validate_migration_intent,
)


# ── _get_category ─────────────────────────────────────────────────────────────

def test_get_category_aws_storage():
    assert _get_category("aws_s3_bucket") == "object_storage"

def test_get_category_aws_relational():
    assert _get_category("aws_rds_cluster") == "database_relational"

def test_get_category_azure_nosql():
    assert _get_category("azurerm_cosmosdb_account") == "database_nosql"

def test_get_category_azure_containers():
    assert _get_category("azurerm_kubernetes_cluster") == "container_orchestration"

def test_get_category_unknown_returns_none():
    assert _get_category("aws_totally_unknown_service") is None

def test_get_category_case_insensitive():
    assert _get_category("AWS_S3_BUCKET") == "object_storage"


# ── _get_sdk_category ─────────────────────────────────────────────────────────

def test_get_sdk_category_s3():
    assert _get_sdk_category("s3") == "object_storage"

def test_get_sdk_category_rds():
    assert _get_sdk_category("rds") == "database_relational"

def test_get_sdk_category_lambda():
    assert _get_sdk_category("lambda") == "serverless_function"

def test_get_sdk_category_unknown():
    assert _get_sdk_category("nonexistent_client") is None


# ── validate_migration_intent — valid mappings ────────────────────────────────

def _plan(*services):
    return {"services": list(services)}

def _svc(name, src, tgt, strategy="REPLATFORM", **kw):
    return {"service_name": name, "source_service": src, "target_service": tgt, "strategy_7r": strategy, **kw}


def test_no_mismatch_relational_to_relational():
    plan = _plan(_svc("db", "aws_rds_cluster", "azurerm_postgresql_flexible_server"))
    assert validate_migration_intent(plan) == []

def test_no_mismatch_storage_to_storage():
    plan = _plan(_svc("bucket", "aws_s3_bucket", "azurerm_storage_account"))
    assert validate_migration_intent(plan) == []

def test_no_mismatch_vm_to_vm():
    plan = _plan(_svc("vm", "aws_instance", "azurerm_linux_virtual_machine"))
    assert validate_migration_intent(plan) == []

def test_no_mismatch_eks_to_aks():
    plan = _plan(_svc("k8s", "aws_eks_cluster", "azurerm_kubernetes_cluster", "REHOST"))
    assert validate_migration_intent(plan) == []

def test_no_mismatch_graph_to_nosql_allowed():
    """Neptune → CosmosDB is allowed: graph_db compatible with nosql (Gremlin API)."""
    plan = _plan(_svc("graph", "aws_neptune_cluster", "azurerm_cosmosdb_account", "REFACTOR"))
    assert validate_migration_intent(plan) == []


# ── validate_migration_intent — mismatches ────────────────────────────────────

def test_mismatch_relational_to_nosql():
    plan = _plan(_svc("db", "aws_rds_cluster", "azurerm_cosmosdb_account", "REFACTOR"))
    result = validate_migration_intent(plan)
    assert len(result) == 1
    assert result[0].source_category == "database_relational"
    assert result[0].target_category == "database_nosql"

def test_mismatch_severity_error_when_refactor():
    plan = _plan(_svc("db", "aws_rds_cluster", "azurerm_cosmosdb_account", "REFACTOR"))
    result = validate_migration_intent(plan)
    assert result[0].severity == "ERROR"

def test_mismatch_severity_warning_when_replatform():
    plan = _plan(_svc("bucket", "aws_s3_bucket", "azurerm_postgresql_flexible_server", "REPLATFORM"))
    result = validate_migration_intent(plan)
    assert len(result) == 1
    assert result[0].severity == "WARNING"

def test_mismatch_storage_to_compute():
    plan = _plan(_svc("svc", "aws_s3_bucket", "azurerm_linux_virtual_machine", "REPLATFORM"))
    result = validate_migration_intent(plan)
    assert len(result) == 1

def test_mismatch_warehouse_to_function():
    plan = _plan(_svc("svc", "aws_redshift_cluster", "azurerm_linux_function_app", "REFACTOR"))
    result = validate_migration_intent(plan)
    assert len(result) == 1


# ── validate_migration_intent — strategy skip ─────────────────────────────────

def test_retire_strategy_skipped():
    plan = _plan(_svc("svc", "aws_rds_cluster", "azurerm_cosmosdb_account", "RETIRE"))
    assert validate_migration_intent(plan) == []

def test_retain_strategy_skipped():
    plan = _plan(_svc("svc", "aws_rds_cluster", "azurerm_cosmosdb_account", "RETAIN"))
    assert validate_migration_intent(plan) == []


# ── validate_migration_intent — edge cases ────────────────────────────────────

def test_unknown_source_service_skipped():
    plan = _plan(_svc("svc", "aws_custom_unknown", "azurerm_postgresql_flexible_server"))
    assert validate_migration_intent(plan) == []

def test_unknown_target_service_skipped():
    plan = _plan(_svc("svc", "aws_rds_cluster", "azurerm_custom_unknown"))
    assert validate_migration_intent(plan) == []

def test_sdk_calls_inference_detects_mismatch():
    """source_service unknown but sdk_calls='rds' infers database_relational → nosql mismatch."""
    svc = {
        "service_name": "svc",
        "source_service": "custom_rds_wrapper",
        "target_service": "azurerm_cosmosdb_account",
        "strategy_7r": "REFACTOR",
        "sdk_calls": ["rds"],
    }
    result = validate_migration_intent({"services": [svc]})
    assert len(result) == 1
    assert result[0].source_category == "database_relational"

def test_empty_plan():
    assert validate_migration_intent({}) == []

def test_empty_services_list():
    assert validate_migration_intent({"services": []}) == []

def test_resources_key_fallback():
    plan = {"resources": [_svc("db", "aws_rds_cluster", "azurerm_postgresql_flexible_server")]}
    assert validate_migration_intent(plan) == []

def test_multiple_services_partial_mismatch():
    plan = _plan(
        _svc("db1", "aws_rds_cluster", "azurerm_postgresql_flexible_server"),  # OK
        _svc("db2", "aws_rds_cluster", "azurerm_cosmosdb_account", "REFACTOR"),  # mismatch
    )
    result = validate_migration_intent(plan)
    assert len(result) == 1
    assert result[0].service_name == "db2"


# ── mismatches_to_issues ──────────────────────────────────────────────────────

def test_mismatches_to_issues_returns_strings():
    plan = _plan(_svc("db", "aws_rds_cluster", "azurerm_cosmosdb_account", "REFACTOR"))
    mismatches = validate_migration_intent(plan)
    issues = mismatches_to_issues(mismatches)
    assert len(issues) == 1
    assert isinstance(issues[0], str)
    assert "relational" in issues[0]

def test_mismatches_to_issues_empty():
    assert mismatches_to_issues([]) == []
