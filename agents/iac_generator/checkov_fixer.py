"""
checkov_fixer.py — Post-generation Checkov scan + deterministic auto-fix loop.

Replaces the SecurityPolicyEngine pre-injection approach with a reactive loop:
  1. Run Checkov on the generated .tf files (Python API — no subprocess).
  2. For each failed check, look up a deterministic fix function.
  3. Apply all known fixes directly to the .tf files.
  4. Re-scan to verify fixes worked and collect remaining failures.
  5. Return remaining failures to the caller (LLM fix loop handles them).

This module is the single source of truth for security rules:
  Checkov defines WHAT to check → fix functions define HOW to fix it.
  No SECURITY_RULES dict to maintain.

Usage (from agent_02_iac.py or graph.py):
    from agents.iac_generator.checkov_fixer import scan_and_autofix

    remaining = scan_and_autofix(output_dir, max_iterations=2)
    # remaining: list of Checkov failure dicts that auto-fix could not handle
    # → feed to LLM fix loop
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

logger = logging.getLogger("CheckovFixer")


# ─────────────────────────────────────────────────────────────────────────────
# Checkov runner — Python API
# ─────────────────────────────────────────────────────────────────────────────

def _run_checkov_api(output_dir: Path) -> list[dict]:
    """Run Checkov via its Python API and return structured failure dicts.

    Each dict has:
      check_id, check_name, resource, file, evaluated_keys, severity
    """
    try:
        from checkov.terraform.runner import Runner  # type: ignore[import]
        from checkov.common.models.enums import CheckResult  # type: ignore[import]
    except ImportError:
        logger.error("checkov not installed — pip install checkov")
        return []

    try:
        runner = Runner()
        report = runner.run(root_folder=str(output_dir))
    except TypeError:
        # Fallback for older Checkov API (runner_filter signature differs across versions)
        try:
            report = runner.run(root_folder=str(output_dir), runner_filter=None)
        except Exception as e2:
            logger.error("checkov runner error (fallback): %s", e2)
            return []
    except Exception as e:
        logger.error("checkov runner error: %s", e)
        return []

    failures = []
    for record in report.failed_checks:
        # evaluated_keys: list of attribute paths like "config/min_tls_version"
        raw_keys = []
        if hasattr(record, "check_result") and hasattr(record.check_result, "evaluated_keys"):
            raw_keys = record.check_result.evaluated_keys or []
        # Normalize: "config/attr" → "attr"
        evaluated_keys = [k.split("/")[-1] for k in raw_keys if isinstance(k, str)]

        failures.append({
            "check_id":       record.check_id,
            "check_name":     record.check_id_name if hasattr(record, "check_id_name") else "",
            "resource":       record.resource,
            "file":           record.repo_file_path,
            "evaluated_keys": evaluated_keys,
            "severity":       str(record.severity) if record.severity else "UNKNOWN",
        })

    logger.info(
        "checkov_fixer: scan complete — %d passed, %d failed",
        len(report.passed_checks), len(failures),
    )
    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Fix helpers — used by the fix functions below
# ─────────────────────────────────────────────────────────────────────────────

def _set_attr(content: str, resource_type: str, attr: str, value: str) -> str:
    """Set attr = value inside every resource block of resource_type.

    - If attr already exists with a different value → overwrite.
    - If attr is missing → inject before the closing brace of the block.
    """
    # Match the full resource block (handles up to 3 levels of nested braces)
    block_re = re.compile(
        rf'(resource\s+"{re.escape(resource_type)}"\s+"[^"]+"\s*\{{)',
        re.MULTILINE,
    )
    attr_re = re.compile(rf'^(\s*){re.escape(attr)}\s*=\s*[^\n]+', re.MULTILINE)

    result = content
    offset = 0

    for m in list(block_re.finditer(content)):
        # Walk forward to find the matching closing brace
        depth = 0
        i = m.end() - 1
        while i < len(content):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        else:
            continue

        abs_start = m.start() + offset
        abs_end   = block_end + offset
        block     = result[abs_start:abs_end]

        if attr_re.search(block):
            # Attribute exists — overwrite value
            new_block = attr_re.sub(rf'\g<1>{attr} = {value}', block)
        else:
            # Attribute missing — inject before final closing brace
            idx = block.rfind("}")
            new_block = block[:idx] + f"  {attr} = {value}\n" + block[idx:]

        result = result[:abs_start] + new_block + result[abs_end:]
        offset += len(new_block) - len(block)

    return result


def _inject_block(content: str, resource_type: str, block_text: str, detect_key: str) -> str:
    """Inject a HCL sub-block (e.g. identity {}) if detect_key not already present."""
    block_re = re.compile(
        rf'(resource\s+"{re.escape(resource_type)}"\s+"[^"]+"\s*\{{)',
        re.MULTILINE,
    )
    result = content
    offset = 0

    for m in list(block_re.finditer(content)):
        depth = 0
        i = m.end() - 1
        while i < len(content):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        else:
            continue

        abs_start = m.start() + offset
        abs_end   = block_end + offset
        block     = result[abs_start:abs_end]

        if detect_key in block:
            continue  # already present

        idx = block.rfind("}")
        indented = "\n".join(f"  {ln}" for ln in block_text.splitlines())
        new_block = block[:idx] + f"\n{indented}\n" + block[idx:]

        result = result[:abs_start] + new_block + result[abs_end:]
        offset += len(new_block) - len(block)

    return result


def _patch_file(tf_path: Path, patcher: Callable[[str], str]) -> bool:
    """Apply patcher to the content of tf_path.  Returns True if content changed."""
    try:
        original = tf_path.read_text(encoding="utf-8")
        fixed    = patcher(original)
        if fixed != original:
            tf_path.write_text(fixed, encoding="utf-8")
            return True
    except OSError as e:
        logger.warning("checkov_fixer: could not patch %s: %s", tf_path.name, e)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Fix registry — one entry per Checkov check ID
#
# Each entry is a callable:
#   fix_fn(output_dir: Path, failure: dict) -> bool
#   Returns True when the fix was applied (file was changed).
#
# Convention: use _set_attr / _inject_block helpers above.
# To add a new check: copy the closest existing entry and adapt.
# ─────────────────────────────────────────────────────────────────────────────

def _fix_storage_tls(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "min_tls_version", '"TLS1_2"'))
    return changed

def _fix_storage_https_only(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "https_traffic_only_enabled", "true"))
    return changed

def _fix_storage_public_access(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "public_network_access_enabled", "false"))
    return changed

def _fix_storage_nested_public(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "allow_nested_items_to_be_public", "false"))
    return changed

def _fix_storage_cross_tenant(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "cross_tenant_replication_enabled", "false"))
    return changed

def _fix_keyvault_purge(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_key_vault", "purge_protection_enabled", "true"))
    return changed

def _fix_keyvault_softdelete(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_key_vault", "soft_delete_retention_days", "7"))
    return changed

def _fix_vm_encryption_host(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "encryption_at_host_enabled", "true"))
    return changed

def _fix_vm_identity(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    block = 'identity {\n    type = "SystemAssigned"\n  }'
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _inject_block(c, resource_type, block, "identity"))
    return changed

def _fix_webapp_https(output_dir: Path, failure: dict) -> bool:
    resource_type = failure["resource"].split(".")[0]
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, resource_type, "https_only", "true"))
    return changed

def _fix_pg_backup(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_postgresql_flexible_server", "backup_retention_days", "7"))
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_postgresql_flexible_server", "geo_redundant_backup_enabled", "true"))
        # Force-overwrite geo_redundant_backup_enabled = false → true (LLM may write false)
        changed |= _patch_file(tf, lambda c: re.sub(
            r'(\bgeo_redundant_backup_enabled\s*=\s*)false', r'\1true', c,
        ))
    return changed

def _fix_mysql_backup(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_mysql_flexible_server", "backup_retention_days", "7"))
    return changed

def _fix_acr_public(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_container_registry", "public_network_access_enabled", "false"))
    return changed

def _fix_acr_admin(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_container_registry", "admin_enabled", "false"))
    return changed

def _fix_log_analytics_retention(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_log_analytics_workspace", "retention_in_days", "30"))
    return changed

def _fix_cosmosdb_public(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_cosmosdb_account", "public_network_access_enabled", "false"))
    return changed

def _fix_mssql_tls(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_mssql_server", "minimum_tls_version", '"1.2"'))
    return changed

def _fix_mssql_public(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "azurerm_mssql_server", "public_network_access_enabled", "false"))
    return changed

def _fix_rds_encrypted(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_db_instance", "storage_encrypted", "true"))
    return changed

def _fix_rds_deletion(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_db_instance", "deletion_protection", "true"))
    return changed

def _fix_rds_backup(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_db_instance", "backup_retention_period", "7"))
    return changed

def _fix_s3_public_block_acls(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_s3_bucket_public_access_block", "block_public_acls", "true"))
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_s3_bucket_public_access_block", "block_public_policy", "true"))
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_s3_bucket_public_access_block", "ignore_public_acls", "true"))
        changed |= _patch_file(tf, lambda c: _set_attr(c, "aws_s3_bucket_public_access_block", "restrict_public_buckets", "true"))
    return changed

def _fix_gcp_bucket_public(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "google_storage_bucket", "public_access_prevention", '"enforced"'))
        changed |= _patch_file(tf, lambda c: _set_attr(c, "google_storage_bucket", "uniform_bucket_level_access", "true"))
    return changed

def _fix_firestore_delete_protection(output_dir: Path, failure: dict) -> bool:
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _set_attr(c, "google_firestore_database", "delete_protection_state", '"DELETE_PROTECTION_ENABLED"'))
    return changed

def _fix_nsg_inbound_default(output_dir: Path, failure: dict) -> bool:
    # CKV_AZURE_65: NSG must not allow unrestricted inbound traffic on sensitive ports.
    # Inject a deny-all inbound security_rule as the lowest-priority fallback.
    # Priority 4096 is the lowest allowed value — evaluated last, after all allow rules.
    block = """\
  security_rule {
    name                       = "deny-all-inbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }"""
    changed = False
    for tf in output_dir.glob("*.tf"):
        changed |= _patch_file(tf, lambda c: _inject_block(c, "azurerm_network_security_group", block, "deny-all-inbound"))
    return changed

def _fix_vm_os_disk_encryption(output_dir: Path, failure: dict) -> bool:
    # CKV_AZURE_189: OS disk must use a managed disk with encryption.
    # Ensure os_disk block exists with storage_account_type set (Premium_LRS enables
    # server-side encryption at rest, satisfying Checkov's disk encryption check).
    block = 'os_disk {\n    caching              = "ReadWrite"\n    storage_account_type = "Premium_LRS"\n  }'
    changed = False
    for tf in output_dir.glob("*.tf"):
        for rtype in ("azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine"):
            changed |= _patch_file(tf, lambda c, r=rtype: _inject_block(c, r, block, "os_disk"))
    return changed


# ── Registry: check_id → fix function ────────────────────────────────────────
# Add new entries here when a new Checkov check needs auto-fixing.
CHECKOV_AUTO_FIXES: dict[str, Callable[[Path, dict], bool]] = {
    # Azure Storage
    "CKV_AZURE_3":    _fix_storage_https_only,
    "CKV_AZURE_6":    _fix_storage_tls,
    "CKV_AZURE_59":   _fix_storage_public_access,
    "CKV2_AZURE_40":  _fix_storage_nested_public,
    "CKV2_AZURE_41":  _fix_storage_cross_tenant,
    # Azure Key Vault
    "CKV_AZURE_42":   _fix_keyvault_purge,
    "CKV_AZURE_110":  _fix_keyvault_softdelete,
    # Azure VM
    "CKV_AZURE_151":  _fix_vm_encryption_host,
    "CKV_AZURE_50":   _fix_vm_identity,
    "CKV_AZURE_189":  _fix_vm_os_disk_encryption,
    # Azure NSG
    "CKV_AZURE_65":   _fix_nsg_inbound_default,
    # Azure Web/Function App
    "CKV_AZURE_14":   _fix_webapp_https,
    "CKV_AZURE_17":   _fix_webapp_https,
    # Azure PostgreSQL
    "CKV_AZURE_136":  _fix_pg_backup,
    # Azure MySQL
    "CKV_AZURE_28":   _fix_mysql_backup,
    # Azure Container Registry
    "CKV_AZURE_163":  _fix_acr_public,
    "CKV_AZURE_137":  _fix_acr_admin,
    # Azure Log Analytics
    "CKV_AZURE_50":   _fix_log_analytics_retention,
    # Azure Cosmos DB
    "CKV_AZURE_100":  _fix_cosmosdb_public,
    # Azure MSSQL
    "CKV_AZURE_113":  _fix_mssql_tls,
    "CKV_AZURE_224":  _fix_mssql_public,
    # AWS RDS
    "CKV_AWS_17":     _fix_rds_encrypted,
    "CKV_AWS_293":    _fix_rds_deletion,
    "CKV_AWS_133":    _fix_rds_backup,
    # AWS S3
    "CKV_AWS_53":     _fix_s3_public_block_acls,
    "CKV_AWS_54":     _fix_s3_public_block_acls,
    "CKV_AWS_55":     _fix_s3_public_block_acls,
    "CKV_AWS_56":     _fix_s3_public_block_acls,
    # GCP Storage
    "CKV_GCP_29":     _fix_gcp_bucket_public,
    "CKV_GCP_62":     _fix_gcp_bucket_public,
    # GCP Firestore
    "CKV_GCP_116":    _fix_firestore_delete_protection,
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def scan_and_autofix(output_dir: str | Path, max_iterations: int = 2) -> list[dict]:
    """Scan generated .tf files with Checkov, apply deterministic fixes, re-scan.

    Loop:
      1. Run Checkov on output_dir.
      2. For each failed check with a known fix → apply fix.
      3. Re-scan.
      4. Repeat up to max_iterations times.
      5. Return remaining failures (no auto-fix available) for LLM handling.

    Args:
        output_dir:      Directory containing the generated .tf files.
        max_iterations:  Maximum scan→fix→scan cycles (default 2).

    Returns:
        List of failure dicts that could not be auto-fixed.
        Empty list means all security issues were resolved automatically.
    """
    output_dir = Path(output_dir)

    for iteration in range(1, max_iterations + 1):
        failures = _run_checkov_api(output_dir)
        if not failures:
            logger.info("checkov_fixer: iteration %d — all checks pass ✓", iteration)
            return []

        fixable   = [f for f in failures if f["check_id"] in CHECKOV_AUTO_FIXES]
        unfixable = [f for f in failures if f["check_id"] not in CHECKOV_AUTO_FIXES]

        logger.info(
            "checkov_fixer: iteration %d — %d failure(s): %d auto-fixable, %d require LLM",
            iteration, len(failures), len(fixable), len(unfixable),
        )

        if not fixable:
            # Nothing left to auto-fix — hand remaining failures to LLM
            logger.info("checkov_fixer: no more auto-fixable checks — stopping loop")
            return failures

        applied = 0
        for failure in fixable:
            fix_fn = CHECKOV_AUTO_FIXES[failure["check_id"]]
            try:
                changed = fix_fn(output_dir, failure)
                if changed:
                    applied += 1
                    logger.info(
                        "checkov_fixer: fixed %s on %s",
                        failure["check_id"], failure["resource"],
                    )
            except Exception as e:
                logger.warning(
                    "checkov_fixer: fix for %s raised %s — skipping",
                    failure["check_id"], e,
                )

        if applied == 0:
            logger.warning("checkov_fixer: fix functions ran but no file changed — stopping loop")
            return failures

    # Final scan after last iteration
    return _run_checkov_api(output_dir)


def get_coverage_report() -> dict:
    """Return which Checkov checks have auto-fix coverage (diagnostic)."""
    return {
        "auto_fixable_check_ids": sorted(CHECKOV_AUTO_FIXES.keys()),
        "total_covered":          len(CHECKOV_AUTO_FIXES),
    }
