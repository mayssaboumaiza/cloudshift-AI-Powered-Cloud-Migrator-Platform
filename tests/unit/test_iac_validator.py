"""
tests/unit/test_iac_validator.py — Tests unitaires du validateur IaC.

Couvre RNF-5 (Conformité IaC) :
  - fonctions utilitaires testables sans subprocess (pures)
  - comportement quand terraform/checkov sont absents
  - logique de sanitisation HCL
  - logique de split providers / déduplication ressources
  - détection erreurs réseau
  - check_tools_availability

Note : les fonctions qui appellent subprocess (terraform_validate, checkov_scan,
terraform_plan_dryrun, infracost_estimate) sont testées via mock de shutil.which
pour couvrir le chemin "outil absent" sans nécessiter d'installation locale.
"""
import pytest
from unittest.mock import patch, MagicMock

from agents.iac_generator.iac_validator import (
    _sanitize_hcl,
    _split_providers,
    _dedup_resources,
    _is_network_error,
    _write_tf_workspace,
    check_tools_availability,
    terraform_validate,
    checkov_scan,
    terraform_plan_dryrun,
    infracost_estimate,
)


# ── _sanitize_hcl ─────────────────────────────────────────────────────────────

class TestSanitizeHcl:

    def test_literal_backslash_n_replaced_by_newline(self):
        raw = 'description = "line1\\nline2"'
        result = _sanitize_hcl(raw)
        assert "\\n" not in result
        assert "\n" in result

    def test_literal_backslash_t_replaced_by_tab(self):
        raw = 'value = "col1\\tcol2"'
        result = _sanitize_hcl(raw)
        assert "\\t" not in result
        assert "\t" in result

    def test_no_escape_sequences_unchanged(self):
        raw = 'resource "azurerm_storage_account" "main" { name = "mystore" }'
        assert _sanitize_hcl(raw) == raw

    def test_empty_string_unchanged(self):
        assert _sanitize_hcl("") == ""

    def test_multiple_occurrences_all_replaced(self):
        raw = "a\\nb\\nc\\nd"
        result = _sanitize_hcl(raw)
        assert result.count("\n") == 3
        assert "\\n" not in result


# ── _split_providers ──────────────────────────────────────────────────────────

class TestSplitProviders:

    def test_terraform_block_goes_to_provider_part(self):
        content = '''
terraform {
  required_version = ">= 1.5"
}

resource "azurerm_resource_group" "rg" {
  name     = "my-rg"
  location = "westeurope"
}
'''
        provider_part, resource_part = _split_providers(content)
        assert "terraform" in provider_part
        assert "azurerm_resource_group" in resource_part

    def test_provider_block_goes_to_provider_part(self):
        content = '''
provider "azurerm" {
  features {}
}

resource "azurerm_storage_account" "st" {
  name = "mystorage"
}
'''
        provider_part, resource_part = _split_providers(content)
        assert 'provider "azurerm"' in provider_part
        assert "azurerm_storage_account" in resource_part

    def test_no_provider_block_all_goes_to_resource_part(self):
        content = '''
resource "aws_s3_bucket" "b" {
  bucket = "my-bucket"
}
'''
        provider_part, resource_part = _split_providers(content)
        assert provider_part.strip() == ""
        assert "aws_s3_bucket" in resource_part

    def test_empty_content(self):
        provider_part, resource_part = _split_providers("")
        assert provider_part.strip() == ""


# ── _dedup_resources ──────────────────────────────────────────────────────────

class TestDedupResources:

    def test_single_resource_unchanged(self):
        content = '''
resource "azurerm_storage_account" "main" {
  name = "mystore"
}
'''
        seen: set = set()
        result = _dedup_resources(content, seen)
        assert "azurerm_storage_account" in result

    def test_duplicate_resource_removed(self):
        content = '''
resource "azurerm_storage_account" "main" {
  name = "first"
}

resource "azurerm_storage_account" "main" {
  name = "duplicate"
}
'''
        seen: set = set()
        result = _dedup_resources(content, seen)
        assert result.count('resource "azurerm_storage_account" "main"') == 1

    def test_different_names_both_kept(self):
        content = '''
resource "azurerm_storage_account" "store1" {
  name = "first"
}

resource "azurerm_storage_account" "store2" {
  name = "second"
}
'''
        seen: set = set()
        result = _dedup_resources(content, seen)
        assert "store1" in result
        assert "store2" in result

    def test_seen_set_updated(self):
        content = '''
resource "aws_s3_bucket" "logs" {
  bucket = "my-logs"
}
'''
        seen: set = set()
        _dedup_resources(content, seen)
        assert ("aws_s3_bucket", "logs") in seen


# ── _is_network_error ─────────────────────────────────────────────────────────

class TestIsNetworkError:

    @pytest.mark.parametrize("msg", [
        "no such host: registry.terraform.io",
        "lookup registry.terraform.io: Temporary failure in name resolution",
        "connection refused",
        "network is unreachable",
        "dial tcp 1.2.3.4:443: i/o timeout",
        "tls handshake timeout",
        "context deadline exceeded",
        "certificate verify failed",
    ])
    def test_network_patterns_detected(self, msg):
        assert _is_network_error(msg) is True

    @pytest.mark.parametrize("msg", [
        "An argument named 'foo' is not expected here",
        "Error: Invalid expression",
        "Missing required argument",
        "Reference to undeclared resource",
    ])
    def test_non_network_errors_not_detected(self, msg):
        assert _is_network_error(msg) is False

    def test_empty_string_is_not_network_error(self):
        assert _is_network_error("") is False

    def test_case_insensitive(self):
        assert _is_network_error("NO SUCH HOST: registry.terraform.io") is True


# ── _write_tf_workspace ───────────────────────────────────────────────────────

class TestWriteTfWorkspace:

    def test_single_file_creates_main_tf(self):
        """Sans marqueur de fichier, tout va dans main.tf."""
        code = 'resource "aws_s3_bucket" "b" { bucket = "x" }'
        import os, shutil
        tmp = _write_tf_workspace(code)
        try:
            assert os.path.exists(os.path.join(tmp, "main.tf"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_file_markers_create_separate_files(self):
        """Les marqueurs # ─── filename.tf ─── créent des fichiers séparés."""
        code = """
# ─── database.tf ───
resource "azurerm_postgresql_flexible_server" "db" {
  name = "mydb"
}

# ─── storage.tf ───
resource "azurerm_storage_account" "st" {
  name = "mystore"
}
"""
        import os, shutil
        tmp = _write_tf_workspace(code)
        try:
            files = os.listdir(tmp)
            assert "database.tf" in files
            assert "storage.tf" in files
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sanitize_hcl_applied_before_write(self):
        """Les séquences \\n sont remplacées avant l'écriture sur disque."""
        code = 'description = "line1\\nline2"'
        import os, shutil
        tmp = _write_tf_workspace(code)
        try:
            content = open(os.path.join(tmp, "main.tf")).read()
            assert "\\n" not in content
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── check_tools_availability ──────────────────────────────────────────────────

class TestCheckToolsAvailability:

    def test_returns_dict_with_expected_keys(self):
        result = check_tools_availability()
        assert "terraform" in result
        assert "checkov" in result
        assert "infracost" in result
        assert "all_available" in result
        assert "cache_dir" in result

    def test_all_available_false_when_any_missing(self):
        with patch("shutil.which", side_effect=lambda x: None if x == "terraform" else "/usr/bin/" + x):
            result = check_tools_availability()
            assert result["terraform"] is False
            assert result["all_available"] is False

    def test_all_available_true_when_all_present(self):
        with patch("shutil.which", return_value="/usr/bin/tool"):
            result = check_tools_availability()
            assert result["terraform"] is True
            assert result["checkov"] is True
            assert result["infracost"] is True
            assert result["all_available"] is True


# ── Comportement "outil absent" des fonctions publiques ──────────────────────

class TestToolUnavailableFallback:
    """Quand terraform/checkov/infracost ne sont pas installés, les fonctions
    retournent une réponse d'erreur structurée sans lever d'exception."""

    def test_terraform_validate_without_terraform(self):
        with patch("shutil.which", return_value=None):
            result = terraform_validate("resource 'aws_s3_bucket' 'b' {}")
            assert result["valid"] is False
            assert "error" in result
            assert "terraform" in result["error"].lower()

    def test_checkov_scan_without_checkov(self):
        with patch("shutil.which", return_value=None):
            result = checkov_scan("resource 'aws_s3_bucket' 'b' {}")
            assert result["passed"] == 0
            assert result["failed"] == 0
            assert "error" in result
            assert "checkov" in result["error"].lower()

    def test_terraform_plan_dryrun_without_terraform(self):
        with patch("shutil.which", return_value=None):
            result = terraform_plan_dryrun("resource 'aws_s3_bucket' 'b' {}")
            assert result["planned"] is False
            assert "error" in result

    def test_infracost_estimate_without_infracost(self):
        with patch("shutil.which", return_value=None):
            result = infracost_estimate("resource 'aws_s3_bucket' 'b' {}")
            assert result["monthly_cost"] == 0.0
            assert "error" in result

    def test_checkov_scan_returns_structured_dict_on_error(self):
        """Le dict retourné en cas d'erreur contient toujours passed/failed/skipped/failed_checks."""
        with patch("shutil.which", return_value=None):
            result = checkov_scan("")
            assert "passed" in result
            assert "failed" in result
            assert "skipped" in result
            assert "failed_checks" in result
            assert isinstance(result["failed_checks"], list)
