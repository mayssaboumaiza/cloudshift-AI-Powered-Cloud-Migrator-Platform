"""Unit tests for _parse_hcl_files() and _get_attr() in pipeline/nodes/iac_nodes.py."""
from unittest.mock import patch

import pytest

from pipeline.nodes.iac_nodes import _get_attr, _parse_hcl_files

# ── Sample HCL content ────────────────────────────────────────────────────────

_VALID_HCL = '''
resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "pg-prod"
  resource_group_name    = "rg-prod"
  location               = "westeurope"
  backup_retention_days  = 7
  geo_redundant_backup_enabled = true
}

resource "azurerm_storage_account" "logs" {
  name                = "storagelogs"
  resource_group_name = "rg-prod"
  location            = "westeurope"
  account_tier        = "Standard"
}
'''

_MULTI_FILE_HCL = {
    "database.tf": '''
resource "azurerm_mssql_server" "sql" {
  name                = "sql-server"
  location            = "eastus"
  resource_group_name = "rg-sql"
}
''',
    "storage.tf": '''
resource "azurerm_storage_account" "data" {
  name                = "storagedata"
  location            = "eastus"
  resource_group_name = "rg-sql"
  account_tier        = "Premium"
}
''',
}

_HARDCODED_SECRET_HCL = '''
resource "azurerm_postgresql_flexible_server" "bad" {
  name     = "pg-bad"
  password = "SuperSecret123!"
  location = "westeurope"
}
'''


# ── _parse_hcl_files ──────────────────────────────────────────────────────────

def test_parse_empty_input_returns_empty():
    result = _parse_hcl_files({})
    assert result == []


def test_parse_without_hcl2_returns_empty():
    with patch("pipeline.nodes.iac_nodes._HAS_HCL2", False):
        result = _parse_hcl_files({"main.tf": _VALID_HCL})
    assert result == []


def test_parse_valid_hcl_returns_two_resources():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    assert len(result) == 2


def test_parse_resource_types_extracted():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    types = {r["resource_type"] for r in result}
    assert "azurerm_postgresql_flexible_server" in types
    assert "azurerm_storage_account" in types


def test_parse_resource_names_extracted():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    names = {r["resource_name"] for r in result}
    assert "main" in names
    assert "logs" in names


def test_parse_file_name_recorded():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    assert all(r["_file"] == "main.tf" for r in result)


def test_parse_attrs_is_dict():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    for r in result:
        assert isinstance(r["attrs"], dict)


def test_parse_multiple_files():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files(_MULTI_FILE_HCL)
    assert len(result) == 2
    file_names = {r["_file"] for r in result}
    assert "database.tf" in file_names
    assert "storage.tf" in file_names


def test_parse_invalid_hcl_does_not_crash():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"broken.tf": "resource \" broken hcl { <<"})
    assert isinstance(result, list)


def test_parse_mixed_valid_invalid_files():
    pytest.importorskip("hcl2")
    files = {
        "valid.tf": _VALID_HCL,
        "broken.tf": "NOT HCL {{ invalid",
    }
    result = _parse_hcl_files(files)
    # Valid file should still yield resources
    types = {r["resource_type"] for r in result}
    assert "azurerm_postgresql_flexible_server" in types


def test_parse_extracts_backup_retention_days():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"main.tf": _VALID_HCL})
    pg = next(r for r in result if r["resource_type"] == "azurerm_postgresql_flexible_server")
    assert pg["attrs"].get("backup_retention_days") is not None


def test_parse_extracts_hardcoded_password():
    pytest.importorskip("hcl2")
    result = _parse_hcl_files({"bad.tf": _HARDCODED_SECRET_HCL})
    pg = next((r for r in result if r["resource_type"] == "azurerm_postgresql_flexible_server"), None)
    if pg:  # hcl2 parsed successfully
        assert "password" in pg["attrs"]


# ── _get_attr ─────────────────────────────────────────────────────────────────

_SAMPLE_RESOURCES = [
    {
        "resource_type": "azurerm_postgresql_flexible_server",
        "resource_name": "db1",
        "attrs": {"backup_retention_days": 7, "location": "westeurope"},
        "_file": "a.tf",
    },
    {
        "resource_type": "azurerm_postgresql_flexible_server",
        "resource_name": "db2",
        "attrs": {"backup_retention_days": 14, "location": "northeurope"},
        "_file": "b.tf",
    },
    {
        "resource_type": "azurerm_storage_account",
        "resource_name": "store",
        "attrs": {"account_tier": "Standard"},
        "_file": "c.tf",
    },
]


def test_get_attr_finds_single_match():
    vals = _get_attr(_SAMPLE_RESOURCES, "storage_account", "account_tier")
    assert vals == ["Standard"]


def test_get_attr_finds_multiple_matches():
    vals = _get_attr(_SAMPLE_RESOURCES, "postgresql_flexible_server", "backup_retention_days")
    assert sorted(vals) == [7, 14]


def test_get_attr_no_type_match_returns_empty():
    vals = _get_attr(_SAMPLE_RESOURCES, "azurerm_redis_cache", "capacity")
    assert vals == []


def test_get_attr_missing_key_returns_empty():
    vals = _get_attr(_SAMPLE_RESOURCES, "postgresql_flexible_server", "nonexistent_key")
    assert vals == []


def test_get_attr_location_across_all_resources():
    vals = _get_attr(_SAMPLE_RESOURCES, "postgresql_flexible_server", "location")
    assert "westeurope" in vals
    assert "northeurope" in vals


def test_get_attr_empty_resources_returns_empty():
    assert _get_attr([], "anything", "key") == []


def test_get_attr_partial_type_match():
    """resource_type_substr='postgresql' should match 'azurerm_postgresql_flexible_server'."""
    vals = _get_attr(_SAMPLE_RESOURCES, "postgresql", "backup_retention_days")
    assert len(vals) == 2
