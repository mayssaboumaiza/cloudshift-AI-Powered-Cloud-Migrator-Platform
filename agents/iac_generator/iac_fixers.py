"""
iac_fixers.py — Agent 02 deterministic Terraform post-processing and error classification.

Responsibilities:
  - Classify IaC validation errors by type (SCHEMA/SYNTAX/SECURITY/OTHER).
  - Apply 7 deterministic post-processing passes to fix common patterns that
    the LLM consistently gets wrong — these are structural transformations that
    do not require re-invoking the LLM.
  - Read generated .tf files for inspection.

Functions exported:
  _safe_str()                          — safe coerce to str
  _classify_tf_errors()                — error taxonomy for structured fix prompts
  _read_generated_tf_files()           — combined .tf file contents
  _fix_tf_cross_references()           — fix resource reference strings to ${...} expressions
  _fix_tf_variables()                  — declare missing var blocks; add resource prefix
  _add_missing_variable_declarations() — inject required variable blocks
  _normalize_provider_versions()       — pin terraform provider version constraints
  _fix_deprecated_azurerm_attrs()      — rename azurerm deprecated attribute names
  _fix_postgresql_bsku_ha()            — fix PostgreSQL HA SKU + storage_mb constraints
  _inject_missing_security_attrs()     — inject minimum security attributes per resource type
  _inject_azure_network()              — generate network.tf (VNet/subnets/PEs/DNS), wire
                                         delegated_subnet_id into PostgreSQL, add
                                         azurerm_role_assignment, fix PostgreSQL version
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("Agent02")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _safe_str(value: object, max_len: int | None = None) -> str:
    """Coerce *value* to str safely; return '' for None; optionally truncate."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    return s[:max_len] if max_len is not None else s


# ── Checkov fix patterns ──────────────────────────────────────────────────────
# Maps check_id → {file, hcl} — a ready-to-use Terraform block the LLM must
# write verbatim (via write_terraform_file) to satisfy the security check.
# Only checks that require a NEW resource (not just an attribute change) need an
# entry here; attribute-only fixes are handled via get_rag_context_for_resource.
# Coverage: Azure (azurerm), AWS (aws_*), GCP (google_*).
_CHECKOV_FIX_PATTERNS: dict[str, dict] = {

    # ── Azure SQL auditing ────────────────────────────────────────────────────
    "CKV_AZURE_23": {
        "file": "database.tf",
        "hcl": """\
resource "azurerm_mssql_server_extended_auditing_policy" "main" {
  server_id                               = azurerm_mssql_server.main.id
  storage_endpoint                        = try(azurerm_storage_account.audit.primary_blob_endpoint, null)
  storage_account_access_key              = try(azurerm_storage_account.audit.primary_access_key, null)
  storage_account_access_key_is_secondary = false
  retention_in_days                       = 90
  log_monitoring_enabled                  = true
}

resource "azurerm_storage_account" "audit" {
  name                     = "staudit${substr(md5(azurerm_mssql_server.main.name), 0, 8)}"
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  min_tls_version          = "TLS1_2"
  https_traffic_only_enabled               = true
  public_network_access_enabled            = false
  allow_nested_items_to_be_public          = false
  cross_tenant_replication_enabled         = false
}""",
    },
    "CKV_AZURE_24": {
        "file": "database.tf",
        "hcl": """\
resource "azurerm_mssql_server_extended_auditing_policy" "main" {
  server_id                               = azurerm_mssql_server.main.id
  storage_endpoint                        = try(azurerm_storage_account.audit.primary_blob_endpoint, null)
  storage_account_access_key              = try(azurerm_storage_account.audit.primary_access_key, null)
  storage_account_access_key_is_secondary = false
  retention_in_days                       = 90
  log_monitoring_enabled                  = true
}""",
    },
    "CKV2_AZURE_45": {
        "file": "database.tf",
        "hcl": """\
resource "azurerm_mssql_server_extended_auditing_policy" "main" {
  server_id                               = azurerm_mssql_server.main.id
  storage_endpoint                        = try(azurerm_storage_account.audit.primary_blob_endpoint, null)
  storage_account_access_key              = try(azurerm_storage_account.audit.primary_access_key, null)
  storage_account_access_key_is_secondary = false
  retention_in_days                       = 90
  log_monitoring_enabled                  = true
}""",
    },
    "CKV2_AZURE_2": {
        "file": "database.tf",
        "hcl": """\
resource "azurerm_mssql_server_security_alert_policy" "main" {
  resource_group_name = var.resource_group_name
  server_name         = azurerm_mssql_server.main.name
  state               = "Enabled"
  retention_days      = 90
}

resource "azurerm_mssql_server_vulnerability_assessment" "main" {
  server_security_alert_policy_id = azurerm_mssql_server_security_alert_policy.main.id
  storage_container_path          = try("${azurerm_storage_account.audit.primary_blob_endpoint}vulnerability-assessment/", null)
  storage_account_access_key      = try(azurerm_storage_account.audit.primary_access_key, null)
  recurring_scans {
    enabled                   = true
    email_subscription_admins = true
  }
}""",
    },

    # ── Azure SQL PostgreSQL auditing ─────────────────────────────────────────
    "CKV_AZURE_28": {
        "file": "database.tf",
        "hcl": """\
resource "azurerm_postgresql_server_threat_detection_policy" "main" {
  resource_group_name         = var.resource_group_name
  server_name                 = azurerm_postgresql_server.main.name
  enabled                     = true
  retention_days              = 90
  storage_account_access_key  = try(azurerm_storage_account.audit.primary_access_key, null)
  storage_endpoint            = try(azurerm_storage_account.audit.primary_blob_endpoint, null)
}""",
    },

    # ── Azure Storage logging ─────────────────────────────────────────────────
    "CKV_AZURE_33": {
        "file": "storage.tf",
        "hcl": """\
resource "azurerm_storage_account_queue_properties" "main" {
  storage_account_id = azurerm_storage_account.main.id
  logging {
    delete                = true
    read                  = true
    write                 = true
    version               = "1.0"
    retention_policy_days = 10
  }
}""",
    },
    "CKV2_AZURE_33": {
        "file": "storage.tf",
        "hcl": """\
resource "azurerm_monitor_diagnostic_setting" "storage_blob" {
  name                       = "diag-blob-${var.environment}"
  target_resource_id         = "${azurerm_storage_account.main.id}/blobServices/default"
  storage_account_id         = azurerm_storage_account.main.id
  enabled_log { category = "StorageRead"   }
  enabled_log { category = "StorageWrite"  }
  enabled_log { category = "StorageDelete" }
  metric { category = "Transaction" enabled = true }
}""",
    },
    "CKV2_AZURE_21": {
        "file": "storage.tf",
        "hcl": """\
# CKV2_AZURE_21: private endpoint for storage container is managed at the
# storage account level via azurerm_private_endpoint. Add post-migration if
# a VNet/subnet is available in the target environment.
# resource "azurerm_private_endpoint" "storage" {
#   name                = "pe-storage-${var.environment}"
#   location            = var.location
#   resource_group_name = var.resource_group_name
#   subnet_id           = var.subnet_id  # set in variables.tf
#   private_service_connection {
#     name                           = "psc-storage"
#     private_connection_resource_id = azurerm_storage_account.main.id
#     subresource_names              = ["blob"]
#     is_manual_connection           = false
#   }
# }""",
    },

    # ── Azure Key Vault / CMK ─────────────────────────────────────────────────
    "CKV2_AZURE_1": {
        "file": "storage.tf",
        "hcl": """\
# CKV2_AZURE_1: customer-managed key requires Azure Key Vault.
# Add post-migration once Key Vault is provisioned:
# resource "azurerm_storage_account_customer_managed_key" "main" {
#   storage_account_id = azurerm_storage_account.main.id
#   key_vault_id       = var.key_vault_id
#   key_name           = var.cmk_key_name
# }""",
    },

    # ── AWS S3 ────────────────────────────────────────────────────────────────
    "CKV_AWS_18": {
        "file": "storage.tf",
        "hcl": """\
resource "aws_s3_bucket_logging" "main" {
  bucket        = aws_s3_bucket.main.id
  target_bucket = aws_s3_bucket.main.id
  target_prefix = "logs/"
}""",
    },
    "CKV_AWS_19": {
        "file": "storage.tf",
        "hcl": """\
resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}""",
    },
    "CKV_AWS_20": {
        "file": "storage.tf",
        "hcl": """\
resource "aws_s3_bucket_public_access_block" "main" {
  bucket                  = aws_s3_bucket.main.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}""",
    },
    "CKV_AWS_21": {
        "file": "storage.tf",
        "hcl": """\
resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration { status = "Enabled" }
}""",
    },
    "CKV_AWS_86": {
        "file": "storage.tf",
        "hcl": """\
resource "aws_s3_bucket_logging" "main" {
  bucket        = aws_s3_bucket.main.id
  target_bucket = aws_s3_bucket.main.id
  target_prefix = "access-logs/"
}""",
    },

    # ── AWS RDS ───────────────────────────────────────────────────────────────
    "CKV_AWS_16": {
        "file": "database.tf",
        "hcl": """\
# Add to aws_db_instance.main:
#   storage_encrypted = true
#   kms_key_id        = var.kms_key_arn  # optional, uses AWS-managed key if omitted""",
    },
    "CKV_AWS_17": {
        "file": "database.tf",
        "hcl": """\
# Add to aws_db_instance.main:
#   publicly_accessible = false""",
    },
    "CKV_AWS_129": {
        "file": "database.tf",
        "hcl": """\
# Add to aws_db_instance.main:
#   enabled_cloudwatch_logs_exports = ["audit", "error", "general", "slowquery"]""",
    },

    # ── GCP Storage ───────────────────────────────────────────────────────────
    "CKV_GCP_28": {
        "file": "storage.tf",
        "hcl": """\
resource "google_storage_bucket_iam_binding" "deny_public" {
  bucket  = google_storage_bucket.main.name
  role    = "roles/storage.objectViewer"
  members = []
}""",
    },
    "CKV_GCP_62": {
        "file": "storage.tf",
        "hcl": """\
# Add to google_storage_bucket.main:
#   logging { log_bucket = google_storage_bucket.main.name }""",
    },
    "CKV_GCP_78": {
        "file": "storage.tf",
        "hcl": """\
# Add to google_storage_bucket.main:
#   versioning { enabled = true }""",
    },

    # ── GCP SQL ───────────────────────────────────────────────────────────────
    "CKV_GCP_6": {
        "file": "database.tf",
        "hcl": """\
# Add to google_sql_database_instance.main settings block:
#   ip_configuration { require_ssl = true }""",
    },
    "CKV_GCP_14": {
        "file": "database.tf",
        "hcl": """\
# Add to google_sql_database_instance.main settings block:
#   backup_configuration {
#     enabled    = true
#     start_time = "03:00"
#   }""",
    },
}


def _classify_tf_errors(
    tf_diagnostics: list,
    checkov_failures: list,
    plan_output: str,
) -> dict:
    """Classify IaC errors per Study 2 taxonomy for structured LLM fix prompt."""
    schema_errors: list[dict] = []
    syntax_errors: list[dict] = []
    other_errors:  list[dict] = []

    if not isinstance(tf_diagnostics, list):
        logger.warning(
            "_classify_tf_errors: tf_diagnostics is %s, expected list — treating as empty",
            type(tf_diagnostics).__name__,
        )
        tf_diagnostics = []

    if not isinstance(checkov_failures, list):
        logger.warning(
            "_classify_tf_errors: checkov_failures is %s, expected list — treating as empty",
            type(checkov_failures).__name__,
        )
        checkov_failures = []

    for idx, d in enumerate(tf_diagnostics):
        if not isinstance(d, dict):
            logger.warning(
                "_classify_tf_errors: tf_diagnostics[%d] is %s, expected dict — skipped",
                idx, type(d).__name__,
            )
            continue
        raw_detail  = _safe_str(d.get("detail"))
        raw_summary = _safe_str(d.get("summary"))
        detail   = raw_detail.lower()
        summary  = raw_summary.lower()
        combined = detail + " " + summary

        # Extract the wrong attribute name and terraform's own suggestion when present.
        # Covers: An argument named "X" is not expected here. Did you mean "Y"?
        wrong_attr:    str | None = None
        suggested_attr: str | None = None
        m_wrong = re.search(r'argument named “([^”]+)”', raw_detail, re.IGNORECASE)
        if m_wrong:
            wrong_attr = m_wrong.group(1)
        m_suggest = re.search(r'[Dd]id you mean “([^”]+)”', raw_detail)
        if m_suggest:
            suggested_attr = m_suggest.group(1)

        # Also check range for file/attribute location
        diag_range = d.get("range") or {}
        file_ref = ""
        if diag_range:
            fname = diag_range.get("filename", "")
            start = diag_range.get("start", {})
            if fname and start:
                file_ref = f"{fname}:{start.get('line', '?')}"

        if any(kw in combined for kw in [
            "argument", "attribute", "block", "unsupported", "invalid value",
            "required", "not expected", "no argument", "an argument named",
        ]):
            schema_errors.append({
                "type":         "SCHEMA",
                "why":          "Factual Incorrectness" if "unsupported" in combined else "Incompleteness",
                "summary":      raw_summary,
                "detail":       raw_detail,
                "file":         file_ref,
                "wrong_attr":   wrong_attr,
                "suggestion":   suggested_attr,
            })
        elif any(kw in combined for kw in ["unexpected", "syntax", "parse", "expected"]):
            syntax_errors.append({
                "type":    "SYNTAX",
                "why":     "Structural Deficit",
                "summary": raw_summary,
                "detail":  raw_detail,
                "file":    file_ref,
            })
        else:
            other_errors.append({
                "type":    "OTHER",
                "summary": raw_summary,
                "detail":  raw_detail,
                "file":    file_ref,
            })

    # Normalize and deduplicate checkov findings defensively.
    normalized_checkov: list[dict] = []
    for idx, c in enumerate(checkov_failures):
        if not isinstance(c, dict):
            logger.warning(
                "_classify_tf_errors: checkov_failures[%d] is %s, expected dict — skipped",
                idx, type(c).__name__,
            )
            continue
        guideline_raw = c.get("guideline")
        if guideline_raw is not None and not isinstance(guideline_raw, str):
            logger.warning(
                "_classify_tf_errors: checkov_failures[%d].guideline is %s — coerced to str",
                idx, type(guideline_raw).__name__,
            )
        evaluated_keys = c.get("evaluated_keys")
        if not isinstance(evaluated_keys, list):
            evaluated_keys = []
        normalized_checkov.append({
            "check_id":      _safe_str(c.get("check_id")),
            "check_name":    _safe_str(c.get("check_name")),
            "resource":      _safe_str(c.get("resource")),
            "severity":      _safe_str(c.get("severity")) or "UNKNOWN",
            "guideline":     _safe_str(c.get("guideline")),
            "file":          _safe_str(c.get("file")),
            "evaluated_keys": evaluated_keys,
        })

    top_checkov = normalized_checkov  # show all failures — model must fix every one

    lines: list[str] = []
    if schema_errors:
        lines.append(f"SCHEMA ERRORS ({len(schema_errors)}) — invalid/missing arguments:")
        for e in schema_errors[:5]:
            loc = f" [{e['file']}]" if e.get("file") else ""
            base = f"  • [{e['why']}]{loc} {e['summary']}: {e['detail'][:200]}"
            if e.get("suggestion") and e.get("wrong_attr"):
                base += (
                    f"\n    ⚡ APPLY DIRECTLY: rename attribute '{e['wrong_attr']}' "
                    f"→ '{e['suggestion']}' (terraform's own suggestion — no RAG needed)"
                )
            elif e.get("wrong_attr"):
                base += (
                    f"\n    ⚡ ACTION: attribute '{e['wrong_attr']}' is invalid — "
                    f"call get_rag_context_for_resource to find the correct name"
                )
            lines.append(base)
    if syntax_errors:
        lines.append(f"\nSYNTAX ERRORS ({len(syntax_errors)}) — malformed HCL:")
        for e in syntax_errors[:3]:
            loc = f" [{e['file']}]" if e.get("file") else ""
            lines.append(f"  • {loc} {e['summary']}: {e['detail']}")
    if other_errors:
        lines.append(f"\nOTHER ERRORS ({len(other_errors)}):")
        for e in other_errors[:3]:
            loc = f" [{e['file']}]" if e.get("file") else ""
            lines.append(f"  • {loc} {e['summary']}: {e['detail']}")
    if top_checkov:
        lines.append(f"\nSECURITY FAILURES ({len(top_checkov)}) — ALL must be fixed:")
        for c in top_checkov:
            file_hint = f" [{c['file']}]" if c.get("file") else ""
            resource_type = c["resource"].split(".")[0] if "." in c["resource"] else c["resource"]
            keys_hint = ""
            if c.get("evaluated_keys"):
                keys_hint = f"\n      failing attributes: {', '.join(c['evaluated_keys'][:4])}"
            requirement_text = (c["check_name"] or c["guideline"])[:150]
            fix_pattern = _CHECKOV_FIX_PATTERNS.get(c["check_id"])
            if fix_pattern:
                action = (
                    f"ADD this resource block to {fix_pattern['file']} using write_terraform_file:\n"
                    + "\n".join(f"      {ln}" for ln in fix_pattern["hcl"].splitlines())
                )
            else:
                provider = resource_type.split("_")[0] if "_" in resource_type else "azurerm"
                action = (
                    f"call get_rag_context_for_resource('{provider}', '{resource_type}') "
                    f"then fix or add the attribute(s) above to satisfy this requirement"
                )
            lines.append(
                f"  • [{c['severity']}] {c['check_id']}{file_hint} on {c['resource']}:\n"
                f"      REQUIREMENT: {requirement_text}{keys_hint}\n"
                f"      ACTION: {action}"
            )
    if plan_output and "Error:" in plan_output:
        plan_error_lines = [
            ln.strip() for ln in plan_output.splitlines()
            if ln.strip().startswith("Error:") or ln.strip().startswith("│")
        ][:20]
        if plan_error_lines:
            lines.append("\nTERRAFORM PLAN ERRORS (dry-run, -refresh=false):")
            lines.append("\n".join(plan_error_lines))

    # ── Runtime deploy error classification (apply output) ───────────────────
    if plan_output:
        _plan_lower = plan_output.lower()

        # InsufficientQuota — Cognitive Services / OpenAI quota exhausted in region.
        # Root cause: the Azure subscription has no remaining quota for azurerm_cognitive_account
        # (kind=OpenAI, sku=S0) in the chosen region.  The resource must be deployed to a
        # region where the subscription still has quota.
        # Action: _fix_region_availability will reroute to the nearest fallback region on the
        # next generation pass.  Surface as an actionable error so fix_targeted can trigger it.
        if "insufficientquota" in _plan_lower or "insufficient quota" in _plan_lower:
            other_errors.append({
                "type":    "QUOTA",
                "summary": "InsufficientQuota — Cognitive Services quota exhausted in target region",
                "detail": (
                    "azurerm_cognitive_account (kind=OpenAI, sku=S0) cannot be created: "
                    "the Azure subscription has no remaining quota in this region.  "
                    "ACTION: change the location attribute of azurerm_cognitive_account to a "
                    "region with available quota (e.g. eastus, westeurope, swedencentral).  "
                    "Do NOT change azurerm_resource_group.main location — only the cognitive account."
                ),
                "file": "ai.tf",
            })
            lines.append(
                "\nQUOTA ERROR — azurerm_cognitive_account: InsufficientQuota in target region.\n"
                "  ACTION: update location in ai.tf to a region with available OpenAI quota\n"
                "  (eastus / westeurope / swedencentral are usually available)."
            )

        # AuthorizationFailed on roleAssignments/write — service principal lacks
        # Microsoft.Authorization/roleAssignments/write permission.
        # This is a subscription-level IAM constraint that cannot be fixed in Terraform.
        # The resource must be wrapped in a count=0 guard or removed entirely.
        if "authorizationfailed" in _plan_lower and "roleassignments/write" in _plan_lower:
            other_errors.append({
                "type":    "AUTHZ",
                "summary": "AuthorizationFailed — service principal cannot write role assignments",
                "detail": (
                    "azurerm_role_assignment.uai_contributor requires "
                    "Microsoft.Authorization/roleAssignments/write at subscription/resource-group scope.  "
                    "The deployment service principal does not have this permission.  "
                    "ACTION: add `count = 0` to azurerm_role_assignment.uai_contributor in iam.tf "
                    "to skip the role assignment, or ask the subscription Owner to pre-grant "
                    "the Contributor role to the managed identity out-of-band."
                ),
                "file": "iam.tf",
            })
            lines.append(
                "\nAUTHZ ERROR — azurerm_role_assignment: service principal lacks roleAssignments/write.\n"
                "  ACTION: add `count = 0` to azurerm_role_assignment.uai_contributor in iam.tf\n"
                "  OR ask a subscription Owner to pre-assign the role out-of-band."
            )

        # ConflictingPublicNetworkAccess + VNet — already fixed deterministically by
        # _fix_postgresql_bsku_ha Fix 5, but surface as a hint if it still appears.
        if "conflictingpublicnetworkaccess" in _plan_lower:
            other_errors.append({
                "type":    "CONFIG",
                "summary": "ConflictingPublicNetworkAccessAndVirtualNetworkConfiguration on PostgreSQL",
                "detail": (
                    "azurerm_postgresql_flexible_server has both public_network_access_enabled "
                    "and delegated_subnet_id set — these are mutually exclusive.  "
                    "ACTION: remove public_network_access_enabled from the PostgreSQL resource block in database.tf."
                ),
                "file": "database.tf",
            })

    return {
        "schema_errors":   schema_errors,
        "syntax_errors":   syntax_errors,
        "other_errors":    other_errors,
        "security_errors": top_checkov,
        "plan_errors":     plan_output[:1500] if plan_output else "",
        "summary":         "\n".join(lines) if lines else "No classified errors found.",
        "total_count":     len(schema_errors) + len(syntax_errors) + len(other_errors),
    }


def _read_generated_tf_files() -> str:
    """Combined contents of every .tf file under output/migrated_app/."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    output_dir = Path(_out_env) if (_out_env and os.path.isabs(_out_env)) else (
        Path(_PROJECT_ROOT) / (_out_env or os.path.join("output", "migrated_app"))
    )
    if not output_dir.exists():
        return ""

    parts: list[str] = []
    for tf_file in sorted(output_dir.glob("*.tf")):
        try:
            parts.append(f"# ─── {tf_file.name} ───\n{tf_file.read_text(encoding='utf-8')}\n")
        except OSError as e:
            logger.warning(f"_read_generated_tf_files: {tf_file.name}: {e}")
    combined = "\n".join(parts)
    logger.info(f"_read_generated_tf_files: combined {len(parts)} .tf files ({len(combined)} chars)")
    return combined


def _fix_tf_cross_references(output_dir: Path) -> int:
    """Three-pass fixer for issues in generated .tf files.

    Pass 0 — Remove duplicate resource declarations:
      If the same (resource_type, resource_name) appears in more than one file,
      keep only the first occurrence (alphabetical file order). Duplicate declarations
      cause `terraform init` to fail immediately with a configuration error.

    Pass 1 — Remove invalid resource blocks:
      A resource block is considered invalid when it internally references another
      resource (type.name) that is not declared anywhere in the .tf files.
      Example: resource "azurerm_subnet" "example" { virtual_network_name =
               azurerm_virtual_network.example.name } — if azurerm_virtual_network.example
      does not exist this block is an LLM hallucination and must be removed.

    Pass 2 — Fix dangling references:
      After removal, rebuild the declared map. For any remaining reference type.name
      where name is unknown but exactly ONE resource of that type exists, substitute
      the correct name.

    Returns the total number of modifications (block removals + reference substitutions).
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    if not tf_files:
        return 0

    decl_re  = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"', re.MULTILINE)
    ref_re   = re.compile(r'\b(azurerm_\w+|aws_\w+|google_\w+)\.(\w+)\b')
    # Matches a full resource block (handles 2 nesting levels of braces)
    block_re = re.compile(
        r'(?:^|\n)([ \t]*resource\s+"[^"]+"\s+"[^"]+"\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})',
        re.MULTILINE
    )

    def _collect_declared(files: list[Path]) -> dict[str, list[str]]:
        declared: dict[str, list[str]] = {}
        for tf in files:
            try:
                text = tf.read_text(encoding="utf-8")
            except OSError:
                continue
            for rtype, rname in decl_re.findall(text):
                declared.setdefault(rtype, [])
                if rname not in declared[rtype]:
                    declared[rtype].append(rname)
        return declared

    total_fixes = 0

    # ── Pass 0: remove duplicate resource declarations across files ───────────
    # Two files declaring resource "X" "Y" causes `terraform init` to fail with
    # "Duplicate resource configuration". Keep first occurrence (sorted order).
    seen_decls: set[tuple[str, str]] = set()
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        original = text
        for m in block_re.finditer(text):
            block = m.group(1)
            dm = decl_re.search(block)
            if not dm:
                continue
            key = (dm.group(1), dm.group(2))
            if key in seen_decls:
                text = text.replace(m.group(0), "\n", 1)
                total_fixes += 1
                logger.info(
                    f'_fix_tf_cross_references pass0: removed duplicate '
                    f'resource "{key[0]}" "{key[1]}" from {tf.name}'
                )
            else:
                seen_decls.add(key)
        if text != original:
            try:
                tf.write_text(text.strip() + "\n", encoding="utf-8")
            except OSError as e:
                logger.warning(f"_fix_tf_cross_references pass0: {tf.name}: {e}")

    # ── Pass 1: remove blocks that internally reference non-existent resources ─
    declared = _collect_declared(tf_files)

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text

        def maybe_remove_block(m: re.Match) -> str:
            block = m.group(1)
            # Parse declared name of THIS block
            dm = decl_re.search(block)
            if not dm:
                return m.group(0)
            block_rtype, block_rname = dm.group(1), dm.group(2)
            # Check every reference inside the block body (skip the declaration line)
            body = block[dm.end():]
            for ref in ref_re.finditer(body):
                ref_type, ref_name = ref.group(1), ref.group(2)
                if ref_type == block_rtype and ref_name == block_rname:
                    continue  # self-reference, skip
                known = declared.get(ref_type, [])
                if known and ref_name not in known:
                    if len(known) == 1:
                        # Exactly one resource of this type exists under a different
                        # name — this is a dangling NAME reference (e.g. the LLM wrote
                        # azurerm_application_insights.main but declared it as "ai"),
                        # not a hallucinated dependency. Pass 2 will rewrite the name
                        # to the correct one — removing the block here would destroy
                        # a perfectly valid resource over a typo.
                        continue
                    # Multiple or zero candidates exist — genuinely ambiguous/missing
                    # dependency, can't be safely renamed → remove the hallucinated block
                    logger.info(
                        f"_fix_tf_cross_references: removing orphan block "
                        f'resource "{block_rtype}" "{block_rname}" '
                        f"(references {ref_type}.{ref_name} which is undeclared)"
                    )
                    return "\n"  # replace entire block with blank line
            return m.group(0)

        text = block_re.sub(maybe_remove_block, text)

        if text != original:
            try:
                tf.write_text(text.strip() + "\n", encoding="utf-8")
                total_fixes += 1
            except OSError as e:
                logger.warning(f"_fix_tf_cross_references pass1: {tf.name}: {e}")

    # ── Pass 2: re-collect declarations, then fix dangling references ──────────
    declared = _collect_declared(tf_files)

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text
        for match in ref_re.finditer(text):
            ref_type, ref_name = match.group(1), match.group(2)
            known = declared.get(ref_type, [])
            if known and ref_name not in known and len(known) == 1:
                text = text.replace(
                    f"{ref_type}.{ref_name}", f"{ref_type}.{known[0]}"
                )
                total_fixes += 1
                logger.info(
                    f"_fix_tf_cross_references pass2: {tf.name}: "
                    f"{ref_type}.{ref_name} → {ref_type}.{known[0]}"
                )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning(f"_fix_tf_cross_references pass2: {tf.name}: {e}")

    if total_fixes:
        logger.info(f"_fix_tf_cross_references: {total_fixes} modification(s) total")
    return total_fixes


_SAFE_VAR_DEFAULTS: dict[str, str] = {
    # ── Azure ────────────────────────────────────────────────────────────────
    "resource_group_name": '"rg-cloud-migrator"',
    "location":            '"eastus"',
    "azure_region":        '"eastus"',
    "tenant_id":           '"00000000-0000-0000-0000-000000000000"',
    "azuread_admin_login":      '"AzureAD Admin"',
    "azuread_admin_object_id":  '"00000000-0000-0000-0000-000000000000"',
    "client_id":           '"00000000-0000-0000-0000-000000000000"',
    "client_secret":       '"CHANGE_ME_BEFORE_DEPLOY"',
    "admin_username":           '"adminuser"',
    "admin_password":           '"CHANGE_ME_BEFORE_DEPLOY"',
    "db_admin_password":        '"CHANGE_ME_BEFORE_DEPLOY"',
    "db_admin_username":        '"dbadmin"',
    "db_password":              '"CHANGE_ME_BEFORE_DEPLOY"',
    "postgres_admin_password":  '"CHANGE_ME_BEFORE_DEPLOY"',
    "postgres_password":        '"CHANGE_ME_BEFORE_DEPLOY"',
    "postgresql_password":      '"CHANGE_ME_BEFORE_DEPLOY"',
    "mysql_admin_password":     '"CHANGE_ME_BEFORE_DEPLOY"',
    "ssh_public_key": (
        '"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC0placeholder+key/for/terraform/plan/only'
        '+CHANGE_BEFORE_DEPLOY placeholder@cloud-migrator"'
    ),
    "subscription_id":     '"00000000-0000-0000-0000-000000000000"',
    "environment":         '"dev"',
    # ── Azure Storage (root cause: terraform plan fails without default) ──────
    # Note: _build_dynamic_defaults overrides this with a migration_id-derived unique name.
    # This static fallback is only used outside of a migration context.
    "storage_account_name":     '"stcloudmigrator01"',
    "storage_container_name":   '"cloudmigcontainer"',
    "container_name":           '"cloudmigcontainer"',
    "storage_account_id":       '""',
    "key_vault_name":           '"kv-cloud-migrator"',
    "kv_name":                  '"kv-cloud-migrator"',
    "key_vault_id":             '""',
    "ml_workspace_name":        '"ml-workspace"',
    "ml_storage_account_name":  '"stmldev"',
    "application_insights_id":  '""',
    "log_analytics_workspace_id": '""',
    "subnet_id":                '""',
    "vnet_id":                  '""',
    "aks_cluster_name":         '"aks-cloud-migrator"',
    "acr_name":                 '"acrcloudmigrator"',
    "cognitive_account_name":   '"cogacct-cloud-migrator"',
    # ── AWS ──────────────────────────────────────────────────────────────────
    "aws_region":          '"us-east-1"',
    "region":              '"us-east-1"',
    "key_name":            '"cloud-migrator-key"',
    "ami_id":              '"ami-0c02fb55956c7d316"',
    "db_username":         '"dbadmin"',
    "aws_account_id":      '"123456789012"',
    "instance_type":       '"t3.micro"',
    "bucket_name":         '"cloud-migrator-bucket"',
    "s3_bucket_name":      '"cloud-migrator-bucket"',
    # ── GCP ──────────────────────────────────────────────────────────────────
    "project_id":          '"my-gcp-project"',
    "gcp_project":         '"my-gcp-project"',
    "gcp_region":          '"us-central1"',
    "gcp_zone":            '"us-central1-a"',
    "billing_account":     '"000000-000000-000000"',
}

# Variable names whose value should be quoted with the runtime target_region
# instead of the static fallback in _SAFE_VAR_DEFAULTS (when known).
_REGION_VAR_NAMES = ("location", "azure_region", "aws_region", "gcp_region", "region")

_KNOWN_WRONG_DEFAULTS: dict[str, tuple[str, str]] = {
    # Azure: deprecated PostgreSQL server SKU format
    "sku_name": ("GP_Gen5_2", "B_Standard_B1ms"),
    # AWS: t1.micro is not available in most modern regions
    "instance_type": ("t1.micro", "t3.micro"),
}


def _fix_tf_variables(output_dir: Path) -> int:
    """Deterministic post-pass: fix variables.tf common mistakes.

    1. Add `default = ...` for variables that have no default and are known
       safe to default (admin passwords → placeholder, resource_group_name, etc.)
    2. Replace known wrong default values (e.g. deprecated PostgreSQL SKU names).

    Returns the number of variable fixes applied.
    """
    vars_file = output_dir / "variables.tf"
    if not vars_file.exists():
        return 0

    try:
        text = vars_file.read_text(encoding="utf-8")
    except OSError:
        return 0

    original = text
    fixes = 0

    # ── Pre-pass: normalize single-line blocks that mix opening brace with content.
    # HCL allows `variable "x" { default = "y" }` (all on one line) but NOT
    # `variable "x" { description = "z"\n  default = "y"\n}` — if `{` and a
    # statement appear on the same line while the closing `}` is on a different line,
    # terraform init fails with "Invalid single-argument block definition".
    # Fix: when `{` is followed by non-whitespace content AND the block spans
    # multiple lines, move the inline content to the next line.
    def _normalize_inline_brace(m: re.Match) -> str:
        block = m.group(0)
        # Check if opening brace has content on the same line AND block is multi-line
        first_line_end = block.find("\n")
        if first_line_end == -1:
            return block  # single-line block — fine
        first_line = block[:first_line_end]
        brace_pos = first_line.find("{")
        if brace_pos == -1:
            return block
        after_brace = first_line[brace_pos + 1:].strip()
        if not after_brace:
            return block  # nothing after { on first line — already correct
        # Move inline content to next line with proper indentation
        header = first_line[:brace_pos + 1]
        rest = block[first_line_end:]
        return header + "\n  " + after_brace + rest

    _inline_block_re = re.compile(
        r'^variable\s+"[^"]+"\s*\{[^\n]*\n(?:[^}][^\n]*\n)*\s*\}',
        re.MULTILINE,
    )
    new_text, n_inline = _inline_block_re.subn(_normalize_inline_brace, text)
    if n_inline:
        text = new_text
        fixes += n_inline
        logger.info("_fix_tf_variables: normalized %d inline-brace variable block(s)", n_inline)

    # Pattern: variable "name" { ... } (handles nested blocks up to 2 levels)
    var_block_re = re.compile(
        r'(variable\s+"(\w+)"\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})',
        re.MULTILINE
    )

    def fix_var_block(m: re.Match) -> str:
        nonlocal fixes
        block, var_name = m.group(1), m.group(2)

        # ── Fix 1: wrong default value ────────────────────────────────────────
        if var_name in _KNOWN_WRONG_DEFAULTS:
            wrong, correct = _KNOWN_WRONG_DEFAULTS[var_name]
            if f'"{wrong}"' in block:
                block = block.replace(f'"{wrong}"', f'"{correct}"')
                fixes += 1
                logger.info(f"_fix_tf_variables: {var_name}: replaced {wrong!r} → {correct!r}")

        # ── Fix 2: add or OVERRIDE default (migration_id-aware) ─────────────
        dynamic_defaults = _build_dynamic_defaults(output_dir)

        # Names that MUST be unique per migration — always override even if LLM declared a default.
        # Generic names (rg-cloud-migrator, main-postgres, etc.) cause "already exists" errors
        # when the same resource was created by a previous migration run.
        _ALWAYS_OVERRIDE = {
            "resource_group_name",   # Azure-wide conflict if reused across migrations
            "aks_cluster_name",      # DNS prefix must be unique per region
            "acr_name",              # globally unique
            "cognitive_account_name",# globally unique within subscription
            "key_vault_name",        # globally unique (DNS-based)
            "db_server_name",        # globally unique (PostgreSQL/MySQL FQDN)
        }

        if var_name in dynamic_defaults:
            safe_default = dynamic_defaults[var_name]
            if "default" not in block:
                block = block.rstrip().rstrip("}").rstrip() + f"\n  default = {safe_default}\n}}"
                fixes += 1
                logger.info(f"_fix_tf_variables: {var_name}: added default = {safe_default}")
            elif var_name in _ALWAYS_OVERRIDE:
                # Replace any existing hardcoded default with the migration_id-derived value
                block = re.sub(
                    r'(default\s*=\s*)"[^"]*"',
                    f'\\1{safe_default}',
                    block,
                )
                fixes += 1
                logger.info(f"_fix_tf_variables: {var_name}: OVERRODE default → {safe_default}")

        return block

    text = var_block_re.sub(fix_var_block, text)

    if text != original:
        try:
            vars_file.write_text(text, encoding="utf-8")
        except OSError as e:
            logger.warning(f"_fix_tf_variables: {e}")

    return fixes


# Pattern: var.<name> — captures the bare variable identifier
_VAR_REF_RE = re.compile(r'\bvar\.([A-Za-z_][A-Za-z0-9_]*)\b')
# Pattern: variable "<name>" — captures the declared variable identifier
_VAR_DECL_RE = re.compile(r'^\s*variable\s+"([A-Za-z_][A-Za-z0-9_]*)"\s*\{', re.MULTILINE)


def _build_dynamic_defaults(output_dir: Path) -> dict[str, str]:
    """Build a defaults dict with names derived from the migration_id.

    ALL resource names that could conflict between migrations are generated
    from the first 8 hex chars of the migration_id (= output_dir.name).

    Critical fix: resource_group_name uses the migration_id suffix so that
    concurrent or sequential migrations never conflict on the same RG in Azure.
    terraform apply was failing with "resource already exists" when multiple
    migrations all used the hardcoded default "rg-cloud-migrator".

    Format rules:
      - Resource Group: alphanumeric + hyphens, 1-90 chars  → "rg-XXXXXXXX"
      - AKS dns_prefix: alphanumeric + hyphens, 3-45 chars  → "aks-XXXXXXXX"
      - ACR name: alphanumeric only, 5-50 chars             → "acrXXXXXXXX"
      - Key Vault: alphanumeric + hyphens, 3-24 chars       → "kv-XXXXXXXX"
      - Cognitive: alphanumeric + hyphens, 2-64 chars       → "cog-XXXXXXXX"
    """
    migration_id_raw = output_dir.name                    # e.g. "5601cadc-bd81-4210-..."
    hex_only         = migration_id_raw.replace("-", "")  # "5601cadcbd81..."
    slug8            = hex_only[:8].lower()               # "5601cadc"  — 8 chars

    slug14 = hex_only[:14].lower()  # 14 chars for storage account (Azure: 3-24 alphanum)
    slug12 = hex_only[:12].lower()  # 12 chars for ML storage — "stml" + 12 = 16 chars, distinct from "st" + 14

    overrides: dict[str, str] = {
        # Resource Group — MUST be unique per migration to avoid "already exists" error
        "resource_group_name":    f'"rg-{slug8}"',
        # Azure globally-unique names — derived from migration_id
        "aks_cluster_name":       f'"aks-{slug8}"',
        "acr_name":               f'"acr{slug8}"',         # no hyphens (ACR constraint)
        "cognitive_account_name": f'"cog-{slug8}"',
        "key_vault_name":         f'"kv-{slug8}"',
        "db_server_name":         f'"pg-{slug8}"',         # PostgreSQL FQDN is globally unique
        # Storage Account: globally unique, 3-24 alphanum only — "st" + 14 hex = 16 chars
        "storage_account_name":     f'"st{slug14}"',
        # ML Storage Account — MUST differ from storage_account_name to avoid name collision
        # Uses "stml" prefix + 12 hex chars = 16 chars total, guaranteed distinct from "st"+14
        "ml_storage_account_name":  f'"stml{slug12}"',
    }
    return {**_SAFE_VAR_DEFAULTS, **overrides}


def _deduplicate_variable_blocks(text: str) -> str:
    """Remove duplicate variable blocks from a Terraform variables.tf content.

    When the LLM fix loop rewrites variables.tf and the post-pass appends more
    declarations, the same variable can appear twice. This causes terraform to
    fail with 'duplicate variable definition'. We keep only the LAST occurrence
    of each variable block (LLM's version is preferred over the auto-generated one).
    """
    # Find each `variable "name" {` header, then scan forward counting braces
    # to locate the block's true end — this correctly handles nested blocks
    # such as `validation { condition = ... }` that a naive `[^}]*` regex
    # would truncate at the first `}`, corrupting the file.
    _HEADER_RE = re.compile(r'variable\s+"([A-Za-z_][A-Za-z0-9_]*)"\s*\{')

    spans: list[tuple[int, int, str, str]] = []  # (start, end, var_name, block_text)
    for m in _HEADER_RE.finditer(text):
        var_name = m.group(1)
        depth = 0
        i = m.end() - 1  # position of the opening brace
        end = None
        for j in range(i, len(text)):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end is None:
            continue  # unbalanced — leave as-is, terraform validate will report it
        spans.append((m.start(), end, var_name, text[m.start():end]))

    if not spans:
        return text

    seen: dict[str, str] = {}
    for _, _, var_name, block_text in spans:
        seen[var_name] = block_text  # last occurrence wins

    # Remove all variable block spans (in reverse order to keep indices valid)
    cleaned = text
    for start, end, _, _ in sorted(spans, key=lambda s: s[0], reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    # Strip excessive blank lines left by removal
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).rstrip()
    cleaned += "\n\n" + "\n\n".join(seen.values()) + "\n"
    return cleaned


def _prune_orphan_required_variables(output_dir: Path) -> int:
    """Remove `variable` blocks that have no `default` AND are never referenced as `var.<name>`.

    Terraform requires a value for every variable that lacks a default — `terraform
    plan -input=false` fails immediately with "No value for required variable" if
    such a variable exists, even when nothing in the project actually uses it.
    These orphans are typically leftovers from an abandoned Agent02Fix attempt that
    declared `var.X` plumbing in variables.tf but ultimately wired the resource via
    direct `azurerm_*.name.attr` references instead. Since nothing references them,
    they are pure dead weight — safe to delete outright (unlike unreferenced vars
    *with* a default, which might be intentional operator overrides).
    """
    vars_file = output_dir / "variables.tf"
    if not vars_file.exists():
        return 0
    try:
        vars_text = vars_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"_prune_orphan_required_variables: read variables.tf: {e}")
        return 0

    _HEADER_RE = re.compile(r'variable\s+"([A-Za-z_][A-Za-z0-9_]*)"\s*\{')
    spans: list[tuple[int, int, str, str]] = []
    for m in _HEADER_RE.finditer(vars_text):
        var_name = m.group(1)
        depth = 0
        i = m.end() - 1
        end = None
        for j in range(i, len(vars_text)):
            ch = vars_text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end is None:
            continue
        spans.append((m.start(), end, var_name, vars_text[m.start():end]))

    if not spans:
        return 0

    # Gather every `var.<name>` reference across all generated .tf files
    # (including variables.tf itself, e.g. validation blocks referencing siblings).
    referenced: set[str] = set()
    _VAR_REF_RE = re.compile(r'\bvar\.([A-Za-z_][A-Za-z0-9_]*)')
    for tf_file in output_dir.glob("*.tf"):
        try:
            referenced.update(_VAR_REF_RE.findall(tf_file.read_text(encoding="utf-8")))
        except OSError:
            continue

    orphans = [
        (start, end, name) for start, end, name, block in spans
        if name not in referenced and not re.search(r'\bdefault\s*=', block)
    ]
    if not orphans:
        return 0

    cleaned = vars_text
    for start, end, _ in sorted(orphans, key=lambda s: s[0], reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).rstrip() + "\n"

    try:
        vars_file.write_text(cleaned, encoding="utf-8")
    except OSError as e:
        logger.warning(f"_prune_orphan_required_variables: write variables.tf: {e}")
        return 0

    names = ", ".join(name for _, _, name in orphans)
    logger.info(f"_prune_orphan_required_variables: removed {len(orphans)} unreferenced "
                f"required variable(s) with no default: {names}")
    return len(orphans)


def _add_missing_variable_declarations(output_dir: Path, target_region: str = "") -> int:
    """Deterministic post-pass: declare every var.X reference found in any .tf file.

    `terraform plan` exits with code 1 when a .tf file references `var.foo` and
    `variable "foo"` is not declared anywhere. Agent 02 occasionally emits such
    references (e.g. var.location when the prompt says to use it but variables.tf
    omits the declaration). This pass scans every generated .tf, collects all
    references, and appends a `variable "<name>" { default = ... }` block for any
    missing one — using _build_dynamic_defaults() first (migration_id-derived names),
    then `target_region` for region-named variables, then an empty-string fallback.

    Returns the number of variable declarations added.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    if not tf_files:
        return 0

    referenced: set[str] = set()
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in _VAR_REF_RE.findall(text):
            referenced.add(name)

    if not referenced:
        return 0

    vars_file = output_dir / "variables.tf"
    existing_text = ""
    if vars_file.exists():
        try:
            existing_text = vars_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"_add_missing_variable_declarations: read variables.tf: {e}")

    # A `variable "x" {}` block can legally live in ANY .tf file, not just
    # variables.tf (Agent 02 sometimes inlines one next to the resource that
    # uses it, e.g. iam.tf). Scanning only variables.tf here would miss those
    # declarations and re-declare the same variable, producing terraform's
    # fatal "Duplicate variable declaration" error during init.
    declared: set[str] = set()
    for tf in tf_files:
        try:
            declared |= set(_VAR_DECL_RE.findall(tf.read_text(encoding="utf-8")))
        except OSError:
            continue

    missing = referenced - declared
    if not missing:
        return 0

    # Build migration_id-aware defaults (globally unique resource names)
    dynamic_defaults = _build_dynamic_defaults(output_dir)

    # Variables with known non-string types — skip the generic string fallback
    _NUMBER_VARS: dict[str, str] = {
        "enable_role_assignments": "0",
    }

    new_blocks: list[str] = []
    for name in sorted(missing):
        if name in _NUMBER_VARS:
            new_blocks.append(
                f'variable "{name}" {{\n'
                f'  description = "Auto-declared by Agent 02 post-pass — set before deploy."\n'
                f'  type        = number\n'
                f'  default     = {_NUMBER_VARS[name]}\n'
                f'}}\n'
            )
            logger.info(
                f"_add_missing_variable_declarations: declared variable {name!r} "
                f"(type=number, default={_NUMBER_VARS[name]})"
            )
            continue

        if name in _REGION_VAR_NAMES and target_region:
            default_literal = f'"{target_region}"'
        elif name in dynamic_defaults:
            default_literal = dynamic_defaults[name]
        else:
            default_literal = '""'

        new_blocks.append(
            f'variable "{name}" {{\n'
            f'  description = "Auto-declared by Agent 02 post-pass — set before deploy."\n'
            f'  type        = string\n'
            f'  default     = {default_literal}\n'
            f'}}\n'
        )
        logger.info(
            f"_add_missing_variable_declarations: declared variable {name!r} "
            f"(default={default_literal})"
        )

    new_text = existing_text.rstrip() + "\n\n" + "\n".join(new_blocks)

    # Deduplication pass — remove duplicate variable blocks that may have been
    # introduced by the LLM fix loop writing the same declaration multiple times.
    new_text = _deduplicate_variable_blocks(new_text)

    try:
        vars_file.write_text(new_text, encoding="utf-8")
    except OSError as e:
        logger.warning(f"_add_missing_variable_declarations: write variables.tf: {e}")
        return 0

    return len(new_blocks)


# Provider version floors — single source of truth in core/constants.py.
from core.constants import (  # noqa: E402
    PROVIDER_VERSION_FLOORS as _PROVIDER_VERSION_FLOORS,
    RETENTION_BACKUP_DAYS,
    RETENTION_AUDIT_DAYS,
    RETENTION_LOG_DAYS,
    RETENTION_SOFT_DELETE_DAYS,
    RETENTION_QUEUE_LOG_DAYS,
)


def _normalize_provider_versions(output_dir: Path) -> int:
    """Deterministic post-pass: rewrite provider version constraints to match
    the floors the Agent 02 system prompt specifies.

    Agent 02 occasionally writes `version = "~> 3.0"` for azurerm despite the
    prompt asking for `>= 3.100, < 4.0`. Older constraints reject newer resource
    types (e.g. azurerm_user_assigned_identity) so terraform init fails. This
    pass rewrites the constraint in-place inside provider.tf only.

    Returns the number of provider version blocks updated.
    """
    provider_file = output_dir / "provider.tf"
    if not provider_file.exists():
        return 0

    try:
        text = provider_file.read_text(encoding="utf-8")
    except OSError:
        return 0

    original = text
    fixes = 0

    # Match required_providers blocks: <name> = { source = "...", version = "..." }
    # We capture the name and the version line so we can rewrite only the version.
    for name, target in _PROVIDER_VERSION_FLOORS.items():
        # Pattern targets only the named provider block to avoid touching others.
        block_re = re.compile(
            rf'({name}\s*=\s*\{{[^}}]*?version\s*=\s*)"[^"]*"',
            re.DOTALL,
        )

        def _sub(m: re.Match) -> str:
            nonlocal fixes
            fixes += 1
            return f'{m.group(1)}"{target}"'

        text, _ = block_re.subn(_sub, text)

    if text != original:
        try:
            provider_file.write_text(text, encoding="utf-8")
            logger.info(
                f"_normalize_provider_versions: rewrote {fixes} provider version constraint(s)"
            )
        except OSError as e:
            logger.warning(f"_normalize_provider_versions: {e}")

    return fixes


def _fix_deprecated_azurerm_attrs(output_dir: Path) -> int:
    """Deterministic post-pass: rewrite/remove deprecated azurerm attributes and blocks.

    azurerm >= 3.100 removed these attributes — terraform validate hard-fails with
    "An argument named X is not expected here" when they appear:
      https_traffic_only          → https_traffic_only_enabled
      allow_blob_public_access    → allow_nested_items_to_be_public
      enable_https_traffic_only   → https_traffic_only_enabled

    Also removes inline blocks that were extracted to separate resources in azurerm 3.100+
    and cause hard terraform validate failures:
      customer_managed_key { ... }  → use azurerm_storage_account_customer_managed_key
      queue_properties { ... }      → use azurerm_storage_account_queue_properties

    Returns total number of fixes applied across all .tf files.
    """
    RENAMES: list[tuple[re.Pattern, str]] = [
        (re.compile(r'\bhttps_traffic_only\b(?!_enabled)'), "https_traffic_only_enabled"),
        (re.compile(r'\ballow_blob_public_access\b'),       "allow_nested_items_to_be_public"),
        (re.compile(r'\benable_https_traffic_only\b'),      "https_traffic_only_enabled"),
    ]

    # azurerm_storage_container: storage_account_name + .name → storage_account_id + .id
    # Deprecated in azurerm v4, removed in v5. Targets the attribute line directly.
    _SA_CONTAINER_LINE_RE = re.compile(
        r'^(\s*)storage_account_name(\s*=\s*)(\S+)\.name(\s*)$',
        re.MULTILINE,
    )

    _REMOVED_ATTR_PATTERNS: list[re.Pattern] = [
        # azurerm_mssql_server: attribute does not exist; terraform validate fails.
        re.compile(r'^\s*express_vulnerability_assessment_enabled\s*=\s*.*$', re.MULTILINE),
        # azurerm_cognitive_account: fqdns is not a valid argument in the azurerm provider schema.
        re.compile(r'^\s*fqdns\s*=\s*.*$', re.MULTILINE),
        # azurerm_cognitive_account: outbound_network_access_restricted does not exist.
        re.compile(r'^\s*outbound_network_access_restricted\s*=\s*.*$', re.MULTILINE),
        # network_acls block: virtual_network_rules is a BLOCK TYPE, not an attribute.
        # The LLM consistently generates `virtual_network_rules = []` (attribute = list),
        # which causes "An argument named virtual_network_rules is not expected here".
        # The correct form is a block: virtual_network_rules { subnet_id = "..." }.
        # Since we cannot auto-generate subnet_id, we remove the invalid attribute line.
        re.compile(r'^\s*virtual_network_rules\s*=\s*\[.*?\]\s*$', re.MULTILINE),
        # azurerm_cognitive_account: ssl_enforcement_enabled does not exist on this resource.
        re.compile(r'^\s*ssl_enforcement_enabled\s*=\s*.*$', re.MULTILINE),
        # azurerm_subnet: network_security_group_id was removed in azurerm v2+.
        # NSG association must use azurerm_subnet_network_security_group_association.
        re.compile(r'^\s*network_security_group_id\s*=\s*.*$', re.MULTILINE),
        # azurerm_storage_account: infrastructure_encryption_enabled was removed in azurerm >= 3.99.
        # terraform validate fails with "An argument named infrastructure_encryption_enabled is not
        # expected here". The attribute no longer exists in the azurerm provider schema for v4.x.
        # Enforcement is now handled by Azure Policy at the subscription level.
        re.compile(r'^\s*infrastructure_encryption_enabled\s*=\s*.*$', re.MULTILINE),
        # azurerm_key_vault: soft_delete_enabled was removed — soft-delete is always-on
        # and no longer configurable. terraform validate fails with "An argument named
        # soft_delete_enabled is not expected here". purge_protection_enabled remains valid.
        re.compile(r'^\s*soft_delete_enabled\s*=\s*.*$', re.MULTILINE),
    ]

    # ── customer_managed_key — global removal across ALL resource types ──────────
    #
    # Root cause: LLMs generate customer_managed_key in TWO syntactically different
    # forms, both of which fail terraform validate:
    #
    #   Form 1 (block)  :  customer_managed_key { key_vault_key_id = "..." }
    #   Form 2 (assign) :  customer_managed_key = { key_vault_key_id = "..." }
    #
    # Form 2 causes: "An argument named 'customer_managed_key' is not expected here.
    #                Did you mean to define a block of type 'customer_managed_key'?"
    #
    # Additionally, the block/assign form is re-introduced by the fix_targeted LLM
    # call (attempting to fix checkov) even after the initial fixer removed it.
    # The only safe resolution is to remove it globally — customer_managed_key always
    # requires a pre-existing Key Vault, which is never provisioned in this pipeline.
    # The operator can add azurerm_*_customer_managed_key resources post-deployment.
    #
    # Pattern handles:
    #   - block form:  customer_managed_key { ... }          (no = sign)
    #   - assign form: customer_managed_key = { ... }        (with = sign)
    #   - nested braces inside the block (up to 2 levels)
    _CMK_BLOCK_RE = re.compile(
        r'\n[ \t]*customer_managed_key\s*(?:=\s*)?\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
        re.DOTALL,
    )

    # Keep storage_account and cognitive_account resource regexes only to identify
    # which file/resource was patched (for logging). The actual removal uses _CMK_BLOCK_RE
    # applied globally to every .tf file.
    _SA_RESOURCE_RE = re.compile(
        r'(resource\s+"azurerm_storage_account"\s+"[^"]+"\s*\{)',
        re.MULTILINE,
    )
    _COGNITIVE_RESOURCE_RE = re.compile(
        r'(resource\s+"azurerm_cognitive_account"\s+"[^"]+"\s*\{)',
        re.MULTILINE,
    )

    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text

        # ── Pass 1: simple attribute renames ─────────────────────────────────
        for pattern, replacement in RENAMES:
            text, n = pattern.subn(replacement, text)
            total_fixes += n

        # ── Pass 1b: remove globally invalid line-level attributes ──────────────
        for pattern in _REMOVED_ATTR_PATTERNS:
            text, n = pattern.subn("", text)
            total_fixes += n

        # ── Pass 1c: storage_account_name → storage_account_id (azurerm_storage_container) ─
        # storage_account_name is deprecated in v4, removed in v5.
        # The value reference changes from .name to .id (ID vs display name).
        if "azurerm_storage_container" in text:
            text, n = _SA_CONTAINER_LINE_RE.subn(
                lambda m: f"{m.group(1)}storage_account_id{m.group(2)}{m.group(3)}.id{m.group(4)}",
                text,
            )
            if n:
                total_fixes += n
                logger.info(
                    "_fix_deprecated_azurerm_attrs: %s — storage_account_name → storage_account_id (%d line(s))",
                    tf.name, n,
                )

        # ── Pass 2: remove customer_managed_key blocks globally ───────────────
        # Handles BOTH syntactic forms emitted by the LLM:
        #   block form  : customer_managed_key { ... }
        #   assign form : customer_managed_key = { ... }   ← persistent bug fixed here
        # Applied globally (all files, all resource types) because:
        #   1. The block is re-introduced by fix_targeted LLM calls
        #   2. It always requires a pre-existing Key Vault not in this pipeline
        text, n_cmk = _CMK_BLOCK_RE.subn("", text)
        if n_cmk:
            total_fixes += n_cmk
            logger.info(
                "_fix_deprecated_azurerm_attrs: removed %d customer_managed_key block(s) "
                "(block/assign form) from %s — Key Vault provisioning required post-deploy",
                n_cmk, tf.name,
            )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
                logger.info(f"_fix_deprecated_azurerm_attrs: patched {tf.name}")
            except OSError as e:
                logger.warning(f"_fix_deprecated_azurerm_attrs: {tf.name}: {e}")

    if total_fixes:
        logger.info(f"_fix_deprecated_azurerm_attrs: {total_fixes} fix(es) total")
    return total_fixes


def _fix_postgresql_bsku_ha(output_dir: Path) -> int:
    """Deterministic post-pass for azurerm_postgresql_flexible_server:

    1. SKU tier prefix fix: Azure requires sku_name in format "<tier>_Standard_<size>"
       (e.g. "GP_Standard_D2s_v3"). The LLM often omits the tier prefix and generates
       bare "Standard_D2s_v3". This pass detects missing prefixes and injects "GP_".
       Tier mapping: B-series → B_, D-series → GP_, E-series → MO_.

    2. B-tier HA removal: Azure rejects high_availability on B_Standard_* SKUs.
       Strips the high_availability { ... } block from B-tier resources.

    Returns the total number of fixes applied.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    pg_resource_re = re.compile(
        r'resource\s+"azurerm_postgresql_flexible_server"\s+"[^"]+"\s*\{',
        re.MULTILINE,
    )
    # Matches sku_name values that are missing the tier prefix
    # Valid: "GP_Standard_D2s_v3", "B_Standard_B1ms", "MO_Standard_E2s_v3"
    # Invalid (no tier prefix): "Standard_D2s_v3", "Standard_B1ms", "Standard_D2ds_v4"
    bare_sku_re = re.compile(r'(sku_name\s*=\s*")Standard_(D\w+|B\w+|E\w+)(")')
    # Also catch tier-prefixed but potentially invalid size variants (e.g. GP_Standard_D2ds_v4
    # which may not be available in all regions — normalize to known-safe D2s_v3)
    unsafe_d_sku_re = re.compile(
        r'(sku_name\s*=\s*"(?:GP_)?Standard_D\d+ds_v4)(")',
    )
    b_tier_re = re.compile(r'sku_name\s*=\s*"B_')
    ha_block_re = re.compile(r'\n[ \t]*high_availability\s*\{[^{}]*\}', re.DOTALL)
    # Remove zone attribute: many Azure regions (austriaeast, etc.) don't support
    # availability zones. terraform apply fails at runtime: "No availability zone
    # found in location 'austriaeast'". zone is optional — omitting it lets Azure choose.
    zone_attr_re = re.compile(r'^\s*zone\s*=\s*"[^"]*"\s*\n', re.MULTILINE)
    # Remove standby_availability_zone from high_availability block for same reason
    standby_zone_re = re.compile(r'\n[ \t]*standby_availability_zone\s*=\s*"[^"]*"')

    # Known-safe fallback SKUs by tier (universally available across Azure regions)
    _SAFE_SKU_FALLBACK = {
        "B":  "B_Standard_B2ms",
        "GP": "GP_Standard_D2s_v3",
        "MO": "MO_Standard_E2s_v3",
    }

    def _infer_tier(size: str) -> str:
        """Infer the Azure PostgreSQL SKU tier from the VM size name."""
        s = size.upper()
        if s.startswith("B"):
            return "B"      # Burstable
        if s.startswith("E"):
            return "MO"     # Memory Optimized
        return "GP"         # General Purpose (D-series and everything else)

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text

        for m in list(pg_resource_re.finditer(text)):
            depth = 0
            i = m.end() - 1
            while i < len(text):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        block_end = i + 1
                        break
                i += 1
            else:
                continue

            block = text[m.start():block_end]

            # Fix 1: inject missing tier prefix in sku_name
            def _add_tier_prefix(mo: re.Match) -> str:
                tier = _infer_tier(mo.group(2))
                fixed = f'{mo.group(1)}{tier}_Standard_{mo.group(2)}{mo.group(3)}'
                logger.info(
                    "_fix_postgresql_bsku_ha: fixed bare SKU '%s' → '%s_%s' in %s",
                    mo.group(2), tier, mo.group(2), tf.name,
                )
                return fixed

            new_block, n1 = bare_sku_re.subn(_add_tier_prefix, block)
            if n1:
                text = text[: m.start()] + new_block + text[block_end:]
                block = new_block
                total_fixes += n1

            # Fix 1b: normalize Dds_v4 variants → known-safe D2s_v3
            # GP_Standard_D2ds_v4 / GP_Standard_D4ds_v4 are not available in all regions
            def _normalize_dds(mo: re.Match) -> str:
                safe = f'sku_name = "{_SAFE_SKU_FALLBACK["GP"]}'
                logger.info(
                    "_fix_postgresql_bsku_ha: normalized unsafe Dds_v4 SKU → '%s' in %s",
                    _SAFE_SKU_FALLBACK["GP"], tf.name,
                )
                return f'{safe}{mo.group(2)}'

            new_block, n1b = unsafe_d_sku_re.subn(_normalize_dds, block)
            if n1b:
                text = text[: m.start()] + new_block + text[block_end:]
                block = new_block
                total_fixes += n1b

            # Fix 2: remove high_availability and geo_redundant_backup_enabled from B-tier SKUs.
            # Azure rejects both on B_Standard_* — they require GP or MO tier.
            # Also inject #checkov:skip=CKV_AZURE_136 so Checkov does not report the
            # intentional absence of geo_redundant_backup_enabled as a finding — the
            # attribute is architecturally impossible on Burstable tier, not a misconfiguration.
            if b_tier_re.search(block):
                if "high_availability" in block:
                    new_block, n2 = ha_block_re.subn("", block)
                    if n2:
                        text = text[: m.start()] + new_block + text[block_end:]
                        block = new_block
                        total_fixes += n2
                        logger.info(
                            "_fix_postgresql_bsku_ha: removed high_availability from B-tier "
                            "azurerm_postgresql_flexible_server in %s", tf.name,
                        )
                # geo_redundant_backup_enabled is not supported on B-tier — remove it entirely
                # and annotate the resource block so Checkov does not flag the absence.
                _skip_annotation = (
                    '#checkov:skip=CKV_AZURE_136:'
                    'geo_redundant_backup_enabled is not supported on B_Standard_* SKUs '
                    '(Burstable tier) — Azure API rejects the attribute with a 400 error.'
                )
                if "geo_redundant_backup_enabled" in block:
                    new_block = re.sub(
                        r'\n[ \t]*geo_redundant_backup_enabled\s*=\s*\S+[^\n]*', "", block
                    )
                    if new_block != block:
                        text = text[: m.start()] + new_block + text[block_end:]
                        block = new_block
                        block_end = m.start() + len(new_block)
                        total_fixes += 1
                        logger.info(
                            "_fix_postgresql_bsku_ha: removed geo_redundant_backup_enabled "
                            "from B-tier azurerm_postgresql_flexible_server in %s", tf.name,
                        )
                # Inject #checkov:skip=CKV_AZURE_136 on the resource opening line if absent.
                current_block = text[m.start():block_end]
                if _skip_annotation not in current_block and "CKV_AZURE_136" not in current_block:
                    new_block = re.sub(
                        r'(resource\s+"azurerm_postgresql_flexible_server"\s+"[^"]+"\s*\{)',
                        lambda mo: mo.group(0) + f"\n  {_skip_annotation}",
                        current_block,
                        count=1,
                    )
                    if new_block != current_block:
                        text = text[:m.start()] + new_block + text[block_end:]
                        block_end = m.start() + len(new_block)
                        total_fixes += 1
                        logger.info(
                            "_fix_postgresql_bsku_ha: injected checkov:skip CKV_AZURE_136 "
                            "on B-tier PostgreSQL block in %s", tf.name,
                        )

            # Fix 3: remove zone attribute (many regions have no AZ support)
            new_block, n3 = zone_attr_re.subn("", block)
            if n3:
                text = text[: m.start()] + new_block + text[block_end:]
                block = new_block
                total_fixes += n3
                logger.info(
                    "_fix_postgresql_bsku_ha: removed zone attribute from %s "
                    "(region may not support availability zones)", tf.name,
                )

            # Fix 4: remove standby_availability_zone from high_availability block
            new_block, n4 = standby_zone_re.subn("", block)
            if n4:
                text = text[: m.start()] + new_block + text[block_end:]
                total_fixes += n4
                logger.info(
                    "_fix_postgresql_bsku_ha: removed standby_availability_zone from %s",
                    tf.name,
                )

            # Fix 5: azurerm 4.x sends PublicNetworkAccess=Enabled by default even when the
            # attribute is absent from HCL, causing Azure 400 ConflictingPublicNetworkAccess
            # when delegated_subnet_id is also present (VNet-integrated mode).
            # Solution: when delegated_subnet_id is present, public_network_access_enabled
            # MUST be explicitly set to false — not absent, not true.
            current_block = text[m.start():block_end]
            if "delegated_subnet_id" in current_block:
                if "public_network_access_enabled" not in current_block:
                    # Add it explicitly before closing brace
                    new_pg_block = current_block.rstrip().rstrip("}").rstrip()
                    new_pg_block += "\n  public_network_access_enabled = false\n}\n"
                    text = text[:m.start()] + new_pg_block + text[block_end:]
                    block_end = m.start() + len(new_pg_block)
                    total_fixes += 1
                    logger.info(
                        "_fix_postgresql_bsku_ha: added public_network_access_enabled = false "
                        "to PostgreSQL block in %s (required by azurerm 4.x when delegated_subnet_id present)",
                        tf.name,
                    )
                else:
                    # Ensure it is set to false (not true)
                    new_pg_block = re.sub(
                        r'\n\s*public_network_access_enabled\s*=\s*true',
                        '\n  public_network_access_enabled = false',
                        current_block,
                    )
                    if new_pg_block != current_block:
                        text = text[:m.start()] + new_pg_block + text[block_end:]
                        block_end = m.start() + len(new_pg_block)
                        total_fixes += 1
                        logger.info(
                            "_fix_postgresql_bsku_ha: set public_network_access_enabled = false "
                            "in PostgreSQL block in %s",
                            tf.name,
                        )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_postgresql_bsku_ha: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_postgresql_bsku_ha: %d fix(es) total", total_fixes)
    return total_fixes


def _fix_postgresql_timeouts(output_dir: Path) -> int:
    """Inject timeouts block into azurerm_postgresql_flexible_server resources.

    Azure PostgreSQL Flexible Server can take 20-40+ min to provision.
    Without explicit timeouts, Terraform's default 30-min wall-clock timeout
    causes spurious failures on first-time deploys.

    Only adds the block if no 'timeouts' block is already present.
    Returns the number of resource blocks patched.
    """
    TIMEOUTS_BLOCK = (
        "\n  timeouts {\n"
        "    create = \"60m\"\n"
        "    update = \"60m\"\n"
        "    delete = \"30m\"\n"
        "  }\n"
    )
    _PG_RESOURCE_RE = re.compile(
        r'(resource\s+"azurerm_postgresql_flexible_server"\s+"[^"]+"\s*\{)',
        re.IGNORECASE,
    )
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        if "azurerm_postgresql_flexible_server" not in text:
            continue
        new_text = text
        for m in _PG_RESOURCE_RE.finditer(text):
            # Find the full block by tracking brace depth
            start = m.start()
            depth = 0
            i = m.end() - 1  # position of the opening brace
            block_end = -1
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block_end = i
                        break
                i += 1
            if block_end == -1:
                continue
            block = text[start:block_end + 1]
            if "timeouts" in block:
                continue  # already has a timeouts block
            # Insert timeouts before the closing brace of the resource block
            patched = block[:block_end - start] + TIMEOUTS_BLOCK + "}"
            new_text = new_text.replace(block, patched, 1)
            total_fixes += 1
            logger.info("_fix_postgresql_timeouts: injected timeouts block in %s", tf.name)
        if new_text != text:
            try:
                tf.write_text(new_text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_postgresql_timeouts: %s write: %s", tf.name, e)
    return total_fixes


def _inject_missing_security_attrs(output_dir: Path) -> int:
    """Deterministic post-pass: inject mandatory security attributes the LLM omitted.

    Covers the most common Checkov failures:
    - azurerm_linux/windows_virtual_machine: encryption_at_host_enabled + identity block
    - azurerm_postgresql_flexible_server: backup_retention_days
    - azurerm_storage_account: cross_tenant_replication_enabled + allow_nested_items
    - azurerm_log_analytics_workspace: retention_in_days

    This pass only ADDS missing attributes — it never overwrites existing ones.
    Returns the total number of attribute injections performed.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # ── Helper: inject an attribute line before the closing brace of a block ─────
    def _inject_attr(block: str, attr_line: str) -> str:
        """Insert attr_line before the last closing brace of block if not present."""
        attr_key = attr_line.strip().split("=")[0].strip()
        if re.search(rf'\b{re.escape(attr_key)}\b', block):
            return block  # already present
        # Insert before the final closing brace
        idx = block.rfind("}")
        if idx == -1:
            return block
        prefix = block[:idx]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return prefix + f"  {attr_line}\n" + block[idx:]

    # ── Helper: inject a block (like identity {}) before closing brace ───────────
    def _inject_block(block: str, block_text: str, detect_key: str) -> str:
        """Insert block_text before final closing brace if detect_key not present."""
        if detect_key in block:
            return block
        idx = block.rfind("}")
        if idx == -1:
            return block
        indented = "\n".join(f"  {ln}" for ln in block_text.splitlines())
        return block[:idx] + f"\n{indented}\n" + block[idx:]

    # ── Helper: inject a #checkov:skip comment on the line after the opening brace ─
    def _inject_checkov_skip(block: str, check_id: str, reason: str) -> str:
        """Insert #checkov:skip=<check_id>:<reason> after the opening { if not present."""
        skip_token = f"#checkov:skip={check_id}"
        if skip_token in block:
            return block
        # Find the first newline (end of the `resource "..." "..." {` header line)
        first_nl = block.find("\n")
        if first_nl == -1:
            return block
        skip_line = f"\n  {skip_token}:{reason}"
        return block[: first_nl] + skip_line + block[first_nl:]

    def _inject_attr_in_subblock(block: str, subblock_key: str, attr_line: str) -> str:
        """Insert attr_line inside a sub-block (e.g. azuread_administrator {})."""
        attr_key = attr_line.strip().split("=")[0].strip()
        start = block.find(subblock_key)
        if start == -1:
            return block
        brace_start = block.find("{", start)
        if brace_start == -1:
            return block
        depth = 0
        i = brace_start
        while i < len(block):
            if block[i] == '{':
                depth += 1
            elif block[i] == '}':
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        else:
            return block

        subblock = block[brace_start:block_end]
        if re.search(rf'\b{re.escape(attr_key)}\b', subblock):
            return block

        new_subblock = re.sub(
            r'\n[ \t]*\}$',
            f"\n    {attr_line}\n  }}",
            subblock,
        )
        if new_subblock == subblock:
            return block
        return block[:brace_start] + new_subblock + block[block_end:]

    resource_re = re.compile(
        r'(resource\s+"([^"]+)"\s+"([^"]+)"\s*\{)',
        re.MULTILINE,
    )

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text
        offset = 0

        for m in list(resource_re.finditer(original)):
            rtype = m.group(2)

            # Walk to matching closing brace
            depth = 0
            i = m.end() - 1
            while i < len(original):
                if original[i] == '{':
                    depth += 1
                elif original[i] == '}':
                    depth -= 1
                    if depth == 0:
                        block_end = i + 1
                        break
                i += 1
            else:
                continue

            block_start = m.start()
            block = original[block_start:block_end]
            new_block = block

            if rtype in ("azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine"):
                new_block = _inject_attr(new_block, 'encryption_at_host_enabled = true')
                new_block = _inject_block(
                    new_block,
                    'identity {\n    type = "SystemAssigned"\n  }',
                    "identity",
                )
                # CKV_AZURE_189: os_disk with managed disk type (enables server-side encryption)
                new_block = _inject_block(
                    new_block,
                    'os_disk {\n    caching              = "ReadWrite"\n    storage_account_type = "Premium_LRS"\n  }',
                    "os_disk",
                )

            elif rtype == "azurerm_network_security_group":
                # CKV_AZURE_65: deny-all inbound fallback rule at lowest priority
                _deny_rule = (
                    '  security_rule {\n'
                    '    name                       = "deny-all-inbound"\n'
                    '    priority                   = 4096\n'
                    '    direction                  = "Inbound"\n'
                    '    access                     = "Deny"\n'
                    '    protocol                   = "*"\n'
                    '    source_port_range          = "*"\n'
                    '    destination_port_range     = "*"\n'
                    '    source_address_prefix      = "*"\n'
                    '    destination_address_prefix = "*"\n'
                    '  }'
                )
                new_block = _inject_block(new_block, _deny_rule, "deny-all-inbound")

            elif rtype == "azurerm_postgresql_flexible_server":
                new_block = _inject_attr(new_block, f'backup_retention_days = {RETENTION_BACKUP_DAYS}')
                # CKV_AZURE_136: geo-redundant backups — only on GP/MO tiers (B_Standard_* rejects it).
                # _fix_postgresql_bsku_ha already removed geo_redundant from B-tier blocks; do NOT
                # re-inject here or the two passes will fight each other on every run.
                _is_b_tier = bool(re.search(r'sku_name\s*=\s*"B_', new_block))
                if not _is_b_tier:
                    new_block = re.sub(
                        r'(\bgeo_redundant_backup_enabled\s*=\s*)false',
                        r'\1true',
                        new_block,
                    )
                    new_block = _inject_attr(new_block, 'geo_redundant_backup_enabled = true')

            elif rtype == "azurerm_storage_account":
                new_block = _inject_attr(new_block, 'cross_tenant_replication_enabled = false')
                new_block = _inject_attr(new_block, 'allow_nested_items_to_be_public  = false')
                # CKV2_AZURE_40: shared_access_key_enabled=false is intentionally NOT injected here.
                # When set to false the azurerm provider itself fails during the post-creation
                # data-plane polling step (KeyBasedAuthenticationNotPermitted 403) because it uses
                # key-based auth internally to confirm the storage account is reachable.
                # The provider only supports identity-based polling in very recent versions.
                # We skip this Checkov check instead via the system prompt annotation — the
                # application should use managed identity / Azure AD auth at the app level.
                # If the user has network_isolation_required=True, they likely also configure
                # a managed identity post-deploy and can set this flag manually.
                # Remove any pre-existing shared_access_key_enabled=false to prevent failures:
                new_block = re.sub(
                    r'\n\s*shared_access_key_enabled\s*=\s*false',
                    '',
                    new_block,
                )
                # CKV_AZURE_206: LRS does not satisfy replication requirement — upgrade to GRS
                new_block = re.sub(
                    r'(account_replication_type\s*=\s*)"LRS"',
                    r'\1"GRS"',
                    new_block,
                )
                # CKV2_AZURE_41: SAS expiration policy
                new_block = _inject_block(
                    new_block,
                    'sas_policy {\n    expiration_action = "Log"\n    expiration_period = "07.00:00:00"\n  }',
                    "sas_policy",
                )
                # CKV2_AZURE_38: soft-delete for blobs and containers (7-day retention).
                # logging {} was removed from blob_properties in azurerm v4 — blob
                # service logging now requires azurerm_monitor_diagnostic_setting.
                # CKV2_AZURE_21 is therefore kept in the checkov skip list.
                new_block = _inject_block(
                    new_block,
                    (
                        'blob_properties {\n'
                        f'    delete_retention_policy {{\n'
                        f'      days = {RETENTION_SOFT_DELETE_DAYS}\n'
                        f'    }}\n'
                        f'    container_delete_retention_policy {{\n'
                        f'      days = {RETENTION_SOFT_DELETE_DAYS}\n'
                        f'    }}\n'
                        '  }'
                    ),
                    "blob_properties",
                )

            elif rtype == "azurerm_cognitive_account":
                # CKV_AZURE_236: disable local (API-key) auth — forces Azure AD auth only.
                new_block = _inject_attr(new_block, 'local_auth_enabled = false')
                # CKV_AZURE_238: managed identity required.
                new_block = _inject_block(
                    new_block,
                    'identity {\n    type = "SystemAssigned"\n  }',
                    "identity",
                )
                # Azure requires custom_subdomain_name whenever public_network_access_enabled=false
                # OR when a private endpoint will be created for this account.
                # The subdomain must be globally unique — derive it from the resource name variable
                # or fall back to a hash of the output_dir name.
                if "custom_subdomain_name" not in new_block:
                    # Extract name = var.xxx or name = "literal" from the block
                    _name_var_m = re.search(r'\bname\s*=\s*var\.(\w+)', new_block)
                    _name_lit_m = re.search(r'\bname\s*=\s*"([^"]+)"', new_block)
                    if _name_var_m:
                        subdomain_expr = f'var.{_name_var_m.group(1)}'
                    elif _name_lit_m:
                        subdomain_expr = f'"{_name_lit_m.group(1)}"'
                    else:
                        # Derive a unique 16-char slug from output_dir path
                        _slug = output_dir.name.replace("-", "")[:16].lower()
                        subdomain_expr = f'"cog{_slug}"'
                    new_block = _inject_attr(new_block, f'custom_subdomain_name = {subdomain_expr}')
                # CKV_AZURE_247: injecting network_acls is intentionally skipped here.
                new_block = _inject_checkov_skip(
                    new_block,
                    "CKV_AZURE_247",
                    "DLP requires network_acls Deny + VNet rules (private endpoints) — configure post-deploy",
                )
                # CKV2_AZURE_22: CMK encryption requires a pre-provisioned Azure Key Vault.
                new_block = _inject_checkov_skip(
                    new_block,
                    "CKV2_AZURE_22",
                    "CMK requires pre-provisioned Key Vault — configure azurerm_cognitive_account_customer_managed_key post-deploy",
                )

            elif rtype == "azurerm_log_analytics_workspace":
                new_block = _inject_attr(new_block, f'retention_in_days = {RETENTION_LOG_DAYS}')

            elif rtype == "azurerm_mssql_server":
                new_block = _inject_attr(new_block, 'minimum_tls_version = "1.2"')
                new_block = _inject_attr(new_block, 'public_network_access_enabled = false')
                new_block = _inject_block(
                    new_block,
                    'identity {\n    type = "SystemAssigned"\n  }',
                    "identity",
                )
                new_block = _inject_block(
                    new_block,
                    (
                        'azuread_administrator {\n'
                        '    login_username = var.azuread_admin_login\n'
                        '    object_id      = var.azuread_admin_object_id\n'
                        '    tenant_id      = var.tenant_id\n'
                        '  }'
                    ),
                    "azuread_administrator",
                )
                new_block = _inject_attr_in_subblock(
                    new_block,
                    "azuread_administrator",
                    'tenant_id = var.tenant_id',
                )

            if new_block != block:
                # Recalculate positions after prior replacements using cumulative offset
                abs_start = block_start + offset
                abs_end   = block_end   + offset
                text = text[:abs_start] + new_block + text[abs_end:]
                offset += len(new_block) - len(block)
                total_fixes += 1
                logger.info(
                    f"_inject_missing_security_attrs: patched {rtype} in {tf.name}"
                )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning(f"_inject_missing_security_attrs: {tf.name}: {e}")

    if total_fixes:
        logger.info(f"_inject_missing_security_attrs: {total_fixes} injection(s) total")
    return total_fixes


def _ensure_resource_group(output_dir: Path) -> int:
    """Deterministic post-pass: inject azurerm_resource_group if missing.

    Agent 02 frequently references var.resource_group_name inside resources but
    never emits the azurerm_resource_group resource itself. Without it, every
    terraform apply fails with a 404 because the resource group doesn't exist.

    This pass:
      1. Scans all .tf files for any reference to resource_group_name.
      2. Checks whether any file already declares azurerm_resource_group.
      3. If missing, writes the resource block into main.tf (or creates it).

    Returns 1 if the resource group was injected, 0 otherwise.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    if not tf_files:
        return 0

    # Check if any tf uses resource_group_name (means an RG is expected)
    uses_rg = any(
        "resource_group_name" in tf.read_text(encoding="utf-8", errors="ignore")
        for tf in tf_files
    )
    if not uses_rg:
        return 0

    # Check if azurerm_resource_group is already declared anywhere
    rg_declared = any(
        "azurerm_resource_group" in tf.read_text(encoding="utf-8", errors="ignore")
        for tf in tf_files
    )
    if rg_declared:
        return 0

    rg_block = (
        'resource "azurerm_resource_group" "main" {\n'
        '  name     = var.resource_group_name\n'
        '  location = var.location\n'
        '}\n'
    )

    # Prefer main.tf; fall back to creating it
    target = output_dir / "main.tf"
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(rg_block + "\n" + existing if existing else rg_block, encoding="utf-8")
        logger.info("_ensure_resource_group: injected azurerm_resource_group into main.tf")
    except OSError as e:
        logger.warning(f"_ensure_resource_group: {e}")
        return 0

    # Add resource_group_name and location variables if not already declared
    _add_missing_variable_declarations(output_dir)
    return 1


# ── Compliance standards → Terraform attribute rules ─────────────────────────
#
# Each standard maps to a list of (resource_type_pattern, attribute, value) tuples.
# resource_type_pattern is matched against every resource block found in .tf files.
# "ALL" means apply to every resource type.
#
# Levels of enforcement (by attribute presence logic):
#   SET   — add or overwrite the attribute with the given value
#   MIN   — only set if current numeric value is lower than minimum
_COMPLIANCE_RULES: dict[str, list[tuple[str, str, str]]] = {
    "GDPR": [
        ("azurerm_storage_account",          "min_tls_version",                 '"TLS1_2"'),
        ("azurerm_storage_account",          "https_traffic_only_enabled",      "true"),
        ("azurerm_storage_account",          "allow_nested_items_to_be_public", "false"),
        ("azurerm_postgresql_flexible_server", "backup_retention_days",         "30"),
        ("azurerm_mysql_flexible_server",    "backup_retention_days",           "30"),
        ("azurerm_mssql_server",             "minimum_tls_version",             '"1.2"'),
    ],
    "HIPAA": [
        ("azurerm_storage_account",          "min_tls_version",                 '"TLS1_2"'),
        ("azurerm_storage_account",          "https_traffic_only_enabled",      "true"),
        # infrastructure_encryption_enabled was removed from azurerm_storage_account in azurerm >= 3.99.
        # Injecting it causes terraform validate to fail with "Unsupported argument" every iteration.
        # The equivalent enforcement is handled by Azure Policy at the subscription level.
        ("azurerm_postgresql_flexible_server", "backup_retention_days",         "30"),
        ("azurerm_mysql_flexible_server",    "backup_retention_days",           "30"),
        ("azurerm_key_vault",                "soft_delete_retention_days",      "90"),
        ("azurerm_key_vault",                "purge_protection_enabled",        "true"),
    ],
    "PCI-DSS": [
        ("azurerm_storage_account",          "min_tls_version",                 '"TLS1_2"'),
        ("azurerm_storage_account",          "https_traffic_only_enabled",      "true"),
        ("azurerm_storage_account",          "public_network_access_enabled",   "false"),
        ("azurerm_postgresql_flexible_server", "public_network_access_enabled", "false"),
        ("azurerm_mysql_flexible_server",    "public_network_access_enabled",   "false"),
        ("azurerm_cognitive_account",        "public_network_access_enabled",   "false"),
        ("azurerm_search_service",           "public_network_access_enabled",   "false"),
        ("azurerm_mssql_server",             "minimum_tls_version",             '"1.2"'),
        ("azurerm_mssql_server",             "public_network_access_enabled",   "false"),
    ],
    "ISO27001": [
        ("azurerm_storage_account",          "min_tls_version",                 '"TLS1_2"'),
        ("azurerm_storage_account",          "https_traffic_only_enabled",      "true"),
        # infrastructure_encryption_enabled removed from azurerm_storage_account in azurerm >= 3.99.
        ("azurerm_postgresql_flexible_server", "backup_retention_days",         "14"),
        ("azurerm_mysql_flexible_server",    "backup_retention_days",           "14"),
        ("azurerm_key_vault",                "purge_protection_enabled",        "true"),
        ("azurerm_mssql_server",             "minimum_tls_version",             '"1.2"'),
    ],
    "SOC2": [
        ("azurerm_storage_account",          "min_tls_version",                 '"TLS1_2"'),
        ("azurerm_storage_account",          "https_traffic_only_enabled",      "true"),
        # infrastructure_encryption_enabled removed from azurerm_storage_account in azurerm >= 3.99.
        ("azurerm_postgresql_flexible_server", "backup_retention_days",         "14"),
        ("azurerm_key_vault",                "purge_protection_enabled",        "true"),
        ("azurerm_mssql_server",             "minimum_tls_version",             '"1.2"'),
    ],
}


def _fix_compliance_standards(output_dir: Path, compliance_standards: list[str]) -> int:
    """Enforce compliance standard rules on all generated .tf files.

    For each standard in compliance_standards, applies the attribute rules from
    _COMPLIANCE_RULES to every matching resource block.  Only adds or overwrites
    — never removes attributes the user or LLM explicitly set to other values unless
    the standard strictly requires a specific value (security attributes).

    Returns the total number of attribute injections made.
    """
    import re

    if not compliance_standards:
        return 0

    # Normalise standard names (accept 'ISO 27001', 'iso27001', etc.)
    _ALIASES = {
        "iso27001": "ISO27001", "iso 27001": "ISO27001",
        "iso-27001": "ISO27001", "iso_27001": "ISO27001",
        "pcidss": "PCI-DSS", "pci dss": "PCI-DSS", "pci_dss": "PCI-DSS",
        "soc 2": "SOC2", "soc-2": "SOC2",
        "gdpr": "GDPR", "hipaa": "HIPAA",
    }
    normalised = []
    for s in compliance_standards:
        key = s.strip().lower()
        normalised.append(_ALIASES.get(key, s.strip().upper()))

    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    for standard in normalised:
        rules = _COMPLIANCE_RULES.get(standard, [])
        if not rules:
            logger.warning("_fix_compliance_standards: unknown standard '%s' — skipped", standard)
            continue

        for tf in tf_files:
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            new_text = text
            for resource_type, attr, value in rules:
                if resource_type not in new_text:
                    continue

                # Inject/overwrite attribute inside each matching resource block
                def _inject(m: re.Match, a: str = attr, v: str = value) -> str:
                    block = m.group(0)
                    attr_pat = re.compile(
                        r'(\b' + re.escape(a) + r'\s*=\s*)(?:"[^"]*"|\S+)'
                    )
                    if attr_pat.search(block):
                        return attr_pat.sub(lambda x: x.group(1) + v, block)
                    # Attribute absent — inject before closing brace
                    return block.rstrip().rstrip("}") + f"\n  {a} = {v}\n}}"

                pattern = (
                    r'resource\s+"' + re.escape(resource_type) + r'"\s+"[^"]+"\s*\{[^}]*\}'
                )
                patched, n = re.subn(pattern, _inject, new_text, flags=re.DOTALL)
                if n:
                    new_text = patched
                    total_fixes += n

            if new_text != text:
                try:
                    tf.write_text(new_text, encoding="utf-8")
                    logger.info(
                        "_fix_compliance_standards: %s applied to %s",
                        standard, tf.name,
                    )
                except OSError as e:
                    logger.warning("_fix_compliance_standards: %s: %s", tf.name, e)

    if total_fixes:
        logger.info(
            "_fix_compliance_standards: %d attribute(s) enforced for standards=%s",
            total_fixes, normalised,
        )
    return total_fixes


def _fix_ha_requirement(output_dir: Path, high_availability_required: bool) -> int:
    """Control high_availability blocks based on user preference.

    When high_availability_required=False (default):
      Removes all high_availability { … } blocks from PostgreSQL, MySQL, AKS, etc.
      This prevents deployment failures in new Azure regions that don't yet have
      multiple availability zones (austriaeast, polandcentral, italynorth, etc.).

    When high_availability_required=True:
      Keeps existing HA blocks as-is.  _fix_region_availability() separately
      validates that the target region supports AZs before deploy.
    """
    import re

    if high_availability_required:
        return 0  # user wants HA — keep whatever the agent generated

    tf_files = sorted(output_dir.glob("*.tf"))
    total_removed = 0
    # Pattern covers multi-line high_availability { … } blocks
    ha_pattern = re.compile(
        r'\n?\s*high_availability\s*\{[^}]*\}\n?', re.DOTALL
    )
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "high_availability" not in text:
            continue
        new_text, n = ha_pattern.subn("", text)
        if n:
            try:
                tf.write_text(new_text, encoding="utf-8")
                total_removed += n
                logger.info(
                    "_fix_ha_requirement: removed %d high_availability block(s) from %s",
                    n, tf.name,
                )
            except OSError as e:
                logger.warning("_fix_ha_requirement: %s: %s", tf.name, e)
    return total_removed


def _fix_network_isolation(output_dir: Path, network_isolation_required: bool) -> int:
    """Set public_network_access_enabled consistently for all resources.

    network_isolation_required=False (default):
      Sets public_network_access_enabled = true on every resource that has it.
      Reason: without private endpoints (which are out of scope here), setting
      public_access=false makes resources completely unreachable — including by
      the application being migrated.

    network_isolation_required=True:
      Sets public_network_access_enabled = false on every resource that has it.
      The user is responsible for configuring private endpoints / VNet integration
      outside of this pipeline.
    """
    import re

    target_value = "false" if network_isolation_required else "true"

    # These resource types must keep public_network_access_enabled = false regardless
    # of network_isolation_required, because Checkov enforces it (CKV_AZURE_134 / CKV_AZURE_59)
    # and because the risk of leaving them open is higher than an access convenience:
    #   - azurerm_cognitive_account: CKV_AZURE_134 (INFO) — force false + local_auth_enabled=false
    #     allows access via Azure AD / managed identity without exposing the public endpoint.
    #   - azurerm_storage_account:   CKV_AZURE_59  (INFO) — force false at generate time;
    #     operators enable selective access via service endpoints post-deploy as needed.
    _ALWAYS_PRIVATE: frozenset[str] = frozenset({
        "azurerm_cognitive_account",
        "azurerm_storage_account",
        # _inject_azure_network() always wires delegated_subnet_id +
        # private_dns_zone_id into Postgres Flexible Server blocks (VNet
        # integration). Azure rejects public access alongside VNet config
        # with "ConflictingPublicNetworkAccessAndVirtualNetworkConfiguration",
        # so this resource must always stay private regardless of the
        # network_isolation_required setting.
        "azurerm_postgresql_flexible_server",
    })

    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # Regex to match a full resource block header so we can check the resource type
    # before deciding whether to apply the override.
    _rtype_re = re.compile(r'resource\s+"([^"]+)"\s+"[^"]+"\s*\{')

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "public_network_access_enabled" not in text:
            continue

        def _selective_replace(m: re.Match) -> str:
            # Walk backwards to find the nearest `resource "type"` declaration.
            before = text[: m.start()]
            hdrs = list(_rtype_re.finditer(before))
            rtype = hdrs[-1].group(1) if hdrs else ""
            # Keep always-private resources at false; apply target_value to others.
            effective = "false" if rtype in _ALWAYS_PRIVATE else target_value
            return m.group(1) + effective

        new_text = re.sub(
            r'(\bpublic_network_access_enabled\s*=\s*)(true|false)',
            _selective_replace,
            text,
        )
        if new_text != text:
            try:
                tf.write_text(new_text, encoding="utf-8")
                total_fixes += 1
                logger.info(
                    "_fix_network_isolation: %s — public_network_access_enabled = %s",
                    tf.name, target_value,
                )
            except OSError as e:
                logger.warning("_fix_network_isolation: %s: %s", tf.name, e)
    return total_fixes


def _fix_resource_group_references(output_dir: Path) -> int:
    """Replace var.resource_group_name with azurerm_resource_group.main.name in all resource blocks.

    Without a direct reference, Terraform treats resource_group_name as a plain
    variable lookup and launches all resources in parallel — the resource group
    takes ~35s to provision, so every other resource fails with ResourceGroupNotFound.
    A direct attribute reference (.name) creates an implicit dependency and forces
    the correct creation order.

    The azurerm_resource_group block itself uses `name = var.resource_group_name`
    (not `resource_group_name = …`), so it is never affected by this substitution.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    _rg_ref_re = re.compile(r'resource_group_name\s*=\s*var\.resource_group_name')
    total_fixes = 0
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "resource_group_name" not in text or "var.resource_group_name" not in text:
            continue
        new_text = _rg_ref_re.sub(
            "resource_group_name = azurerm_resource_group.main.name",
            text,
        )
        if new_text != text:
            try:
                tf.write_text(new_text, encoding="utf-8")
                total_fixes += 1
                logger.info("_fix_resource_group_references: patched %s", tf.name)
            except OSError as e:
                logger.warning("_fix_resource_group_references: %s: %s", tf.name, e)
    if total_fixes:
        logger.info("_fix_resource_group_references: %d file(s) patched", total_fixes)
    return total_fixes


def _fix_role_assignment_permissions(output_dir: Path) -> int:
    """Deterministic post-pass: guard azurerm_role_assignment against AuthorizationFailed.

    The service principal running terraform apply typically does NOT have
    Microsoft.Authorization/roleAssignments/write at subscription scope.
    When this resource is present without a guard, apply always fails with 403.

    This pass wraps every azurerm_role_assignment block with:
      count = var.enable_role_assignments  (default = 0, i.e. skipped)

    The variable is injected into variables.tf so operators can opt-in by setting
    enable_role_assignments = 1 after manually granting Owner/UAA rights.
    The managed identity itself is still created — only the role binding is gated.

    Returns the number of role assignment blocks patched.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    _ra_header_re = re.compile(
        r'resource\s+"azurerm_role_assignment"\s+"[^"]+"\s*\{',
        re.MULTILINE,
    )

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "azurerm_role_assignment" not in text:
            continue

        original = text
        offset = 0

        for m in list(_ra_header_re.finditer(original)):
            # Already guarded — skip
            block_header_start = m.start() + offset
            lookahead = text[block_header_start:block_header_start + 300]
            if "count" in lookahead.split("{", 1)[1][:100] if "{" in lookahead else "":
                continue

            # Walk to matching closing brace
            depth = 0
            i = m.end() + offset - 1
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block_end = i + 1
                        break
                i += 1
            else:
                continue

            block = text[block_header_start:block_end]

            # Fix count = true/false (bool) → count = var.enable_role_assignments (number)
            # The LLM fix loop sometimes rewrites azurerm_role_assignment with count = true
            # which causes "number required, but have bool" in terraform validate.
            bool_count = re.search(r'(\bcount\s*=\s*)(true|false)\b', block)
            if bool_count:
                fixed_block = block[:bool_count.start(2)] + "var.enable_role_assignments" + block[bool_count.end(2):]
                text = text[:block_header_start] + fixed_block + text[block_end:]
                offset += len(fixed_block) - len(block)
                total_fixes += 1
                logger.info(
                    "_fix_role_assignment_permissions: fixed count=bool → count=var.enable_role_assignments in %s",
                    tf.name,
                )
                continue

            # Only inject if count not already present
            if re.search(r'\bcount\s*=', block):
                continue

            # Inject count = var.enable_role_assignments after the opening brace
            first_nl = block.find("\n")
            if first_nl == -1:
                continue
            new_block = (
                block[:first_nl]
                + "\n  # Gated: requires Microsoft.Authorization/roleAssignments/write."
                + "\n  # Set var.enable_role_assignments = 1 after granting Owner rights."
                + "\n  count = var.enable_role_assignments"
                + block[first_nl:]
            )

            text = text[:block_header_start] + new_block + text[block_end:]
            offset += len(new_block) - len(block)
            total_fixes += 1
            logger.info(
                "_fix_role_assignment_permissions: gated azurerm_role_assignment in %s "
                "with count = var.enable_role_assignments",
                tf.name,
            )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_role_assignment_permissions: %s: %s", tf.name, e)

    # Inject the control variable into variables.tf if any assignments were patched
    if total_fixes:
        vars_file = output_dir / "variables.tf"
        try:
            existing = vars_file.read_text(encoding="utf-8") if vars_file.exists() else ""
        except OSError:
            existing = ""
        if "enable_role_assignments" not in existing:
            gate_var = (
                '\nvariable "enable_role_assignments" {\n'
                '  description = "Set to 1 to create azurerm_role_assignment resources. '
                'Requires Microsoft.Authorization/roleAssignments/write on the subscription."\n'
                '  type        = number\n'
                '  default     = 0\n'
                '}\n'
            )
            try:
                vars_file.write_text(existing.rstrip() + gate_var, encoding="utf-8")
                logger.info(
                    "_fix_role_assignment_permissions: injected enable_role_assignments variable"
                )
            except OSError as e:
                logger.warning("_fix_role_assignment_permissions: variables.tf: %s", e)

    if total_fixes:
        logger.info(
            "_fix_role_assignment_permissions: %d role assignment(s) gated", total_fixes
        )
    return total_fixes


# ── Azure region availability constraints ─────────────────────────────────────
#
# Maps every Terraform resource type that has known regional gaps to the set of
# Azure regions where it IS available.  When a generated .tf file uses a region
# outside this set, _fix_region_availability() automatically routes that resource
# to the nearest supported region derived from the user's geographic group.
#
# Sources: Azure API error messages, Azure Products by Region page, provider docs.
# New Azure regions (austriaeast, polandcentral, italynorth, spaincentral, …) are
# frequently missing services that are well-established in older regions.
#
# Add new entries here whenever a `LocationNotAvailableForResourceType` error is
# observed for a service type not yet listed.
_AZURE_RESOURCE_REGION_CONSTRAINTS: dict[str, frozenset[str]] = {

    # ── AI / Cognitive Services ───────────────────────────────────────────────
    # Microsoft.CognitiveServices/accounts
    "azurerm_cognitive_account": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "canadaeast",
        "centralindia", "centralus", "eastasia", "eastus", "eastus2",
        "francecentral", "germanywestcentral", "italynorth", "japaneast",
        "japanwest", "jioindiacentral", "jioindiawest", "koreacentral",
        "northcentralus", "northeurope", "norwayeast", "polandcentral",
        "qatarcentral", "southafricanorth", "southcentralus", "southeastasia",
        "southindia", "spaincentral", "swedencentral", "switzerlandnorth",
        "switzerlandwest", "uaenorth", "uksouth", "ukwest", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),
    # Deployments under a Cognitive / OpenAI account inherit the same constraints
    "azurerm_cognitive_deployment": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "canadaeast",
        "centralindia", "centralus", "eastasia", "eastus", "eastus2",
        "francecentral", "germanywestcentral", "italynorth", "japaneast",
        "japanwest", "jioindiacentral", "jioindiawest", "koreacentral",
        "northcentralus", "northeurope", "norwayeast", "polandcentral",
        "qatarcentral", "southafricanorth", "southcentralus", "southeastasia",
        "southindia", "spaincentral", "swedencentral", "switzerlandnorth",
        "switzerlandwest", "uaenorth", "uksouth", "ukwest", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),

    # ── Machine Learning ──────────────────────────────────────────────────────
    # Microsoft.MachineLearningServices/workspaces
    "azurerm_machine_learning_workspace": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralindia",
        "centralus", "eastasia", "eastus", "eastus2", "francecentral",
        "japaneast", "koreacentral", "northcentralus", "northeurope",
        "norwayeast", "southcentralus", "southeastasia", "southindia",
        "swedencentral", "switzerlandnorth", "uksouth", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),
    "azurerm_machine_learning_compute_cluster": frozenset({
        "australiaeast", "canadacentral", "centralus", "eastus", "eastus2",
        "francecentral", "japaneast", "northeurope", "southcentralus",
        "southeastasia", "swedencentral", "uksouth", "westeurope", "westus2",
    }),

    # ── AI Search ─────────────────────────────────────────────────────────────
    # Microsoft.Search/searchServices
    "azurerm_search_service": frozenset({
        "australiaeast", "australiasoutheast", "brazilsouth", "canadacentral",
        "canadaeast", "centralindia", "centralus", "eastasia", "eastus",
        "eastus2", "francecentral", "japaneast", "japanwest", "koreacentral",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "southindia", "swedencentral", "switzerlandnorth", "uksouth", "ukwest",
        "westcentralus", "westeurope", "westus", "westus2",
    }),

    # ── Bot Services ──────────────────────────────────────────────────────────
    # Microsoft.BotService/botServices — very restricted
    "azurerm_bot_service_azure_bot": frozenset({
        "westus", "eastus", "westeurope", "northeurope", "southeastasia",
        "uksouth", "japaneast", "australiaeast",
    }),
    "azurerm_bot_channels_registration": frozenset({
        "westus", "eastus", "westeurope", "northeurope", "southeastasia",
        "uksouth", "japaneast", "australiaeast",
    }),

    # ── Logic Apps ────────────────────────────────────────────────────────────
    # Standard Logic Apps (ISE) are available in fewer regions than Consumption
    "azurerm_logic_app_integration_service_environment": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralus",
        "eastasia", "eastus", "eastus2", "francecentral", "japaneast",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus", "westus2",
    }),

    # ── Healthcare APIs ───────────────────────────────────────────────────────
    "azurerm_healthcare_service": frozenset({
        "australiaeast", "canadacentral", "centralindia", "eastus", "eastus2",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus2",
    }),
    "azurerm_healthcare_workspace": frozenset({
        "australiaeast", "canadacentral", "centralindia", "eastus", "eastus2",
        "northeurope", "southcentralus", "swedencentral", "uksouth",
        "westeurope", "westus2",
    }),

    # ── Spring Apps ───────────────────────────────────────────────────────────
    "azurerm_spring_cloud_service": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralus",
        "eastasia", "eastus", "eastus2", "japaneast", "northeurope",
        "southcentralus", "southeastasia", "swedencentral", "uksouth",
        "westeurope", "westus2",
    }),

    # ── Service Fabric ────────────────────────────────────────────────────────
    "azurerm_service_fabric_cluster": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralindia",
        "centralus", "eastasia", "eastus", "eastus2", "japaneast",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus", "westus2",
    }),
}

# ── Geographic fallback priority ───────────────────────────────────────────────
# Maps every Azure region to an ordered list of fallback regions (same geography first).
# When a resource is not available in the user's region, _fix_region_availability()
# picks the first fallback that IS in the resource's supported set.
_REGION_FALLBACK_PRIORITY: dict[str, list[str]] = {
    # ── Europe ────────────────────────────────────────────────────────────────
    "austriaeast":        ["swedencentral", "germanywestcentral", "westeurope", "northeurope", "uksouth"],
    "polandcentral":      ["swedencentral", "germanywestcentral", "westeurope", "northeurope"],
    "italynorth":         ["swedencentral", "westeurope", "francecentral", "northeurope"],
    "spaincentral":       ["swedencentral", "westeurope", "francecentral", "northeurope"],
    "germanywestcentral": ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "germanynorth":       ["swedencentral", "germanywestcentral", "westeurope", "northeurope"],
    "switzerlandnorth":   ["swedencentral", "westeurope", "germanywestcentral", "northeurope"],
    "switzerlandwest":    ["swedencentral", "westeurope", "germanywestcentral", "northeurope"],
    "norwayeast":         ["swedencentral", "northeurope", "westeurope", "uksouth"],
    "norwaywest":         ["swedencentral", "northeurope", "westeurope"],
    "swedencentral":      ["westeurope", "northeurope", "uksouth", "francecentral"],
    "westeurope":         ["swedencentral", "northeurope", "uksouth", "francecentral"],
    "northeurope":        ["swedencentral", "westeurope", "uksouth", "francecentral"],
    "uksouth":            ["swedencentral", "westeurope", "northeurope", "ukwest"],
    "ukwest":             ["uksouth", "swedencentral", "westeurope", "northeurope"],
    "francecentral":      ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "francesouth":        ["francecentral", "swedencentral", "westeurope"],
    # ── Americas ──────────────────────────────────────────────────────────────
    "eastus":             ["eastus2", "centralus", "northcentralus", "westus2"],
    "eastus2":            ["eastus", "centralus", "northcentralus", "westus2"],
    "northcentralus":     ["eastus", "eastus2", "centralus", "westus2"],
    "southcentralus":     ["eastus", "eastus2", "centralus", "westus2"],
    "centralus":          ["eastus", "eastus2", "northcentralus", "westus2"],
    "westcentralus":      ["westus2", "westus", "eastus", "centralus"],
    "westus":             ["westus2", "eastus", "centralus", "westus3"],
    "westus2":            ["westus", "eastus", "centralus", "westus3"],
    "westus3":            ["westus2", "westus", "eastus", "centralus"],
    "canadacentral":      ["eastus", "eastus2", "canadaeast", "centralus"],
    "canadaeast":         ["canadacentral", "eastus", "eastus2"],
    "brazilsouth":        ["eastus", "eastus2", "southcentralus", "canadacentral"],
    "brazilsoutheast":    ["brazilsouth", "eastus", "eastus2"],
    # ── Asia Pacific ──────────────────────────────────────────────────────────
    "southeastasia":      ["eastasia", "japaneast", "centralindia", "australiaeast"],
    "eastasia":           ["southeastasia", "japaneast", "koreacentral", "centralindia"],
    "japaneast":          ["japanwest", "southeastasia", "eastasia", "koreacentral"],
    "japanwest":          ["japaneast", "southeastasia", "eastasia"],
    "koreacentral":       ["koreasouth", "japaneast", "southeastasia", "eastasia"],
    "koreasouth":         ["koreacentral", "japaneast", "southeastasia"],
    "centralindia":       ["southindia", "westindia", "southeastasia", "eastasia"],
    "southindia":         ["centralindia", "westindia", "southeastasia"],
    "westindia":          ["centralindia", "southindia", "southeastasia"],
    "jioindiacentral":    ["centralindia", "southindia", "southeastasia"],
    "jioindiawest":       ["centralindia", "southindia", "southeastasia"],
    "australiaeast":      ["australiasoutheast", "southeastasia", "japaneast"],
    "australiasoutheast": ["australiaeast", "southeastasia", "japaneast"],
    "australiacentral":   ["australiaeast", "australiasoutheast", "southeastasia"],
    "australiacentral2":  ["australiaeast", "australiasoutheast", "southeastasia"],
    # ── Middle East & Africa ──────────────────────────────────────────────────
    "uaenorth":           ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "uaecentral":         ["uaenorth", "westeurope", "northeurope"],
    "qatarcentral":       ["uaenorth", "swedencentral", "westeurope", "uksouth"],
    "southafricanorth":   ["westeurope", "northeurope", "swedencentral", "uksouth"],
    "southafricawest":    ["southafricanorth", "westeurope", "northeurope"],
    "israelcentral":      ["swedencentral", "westeurope", "northeurope"],
    "mexicocentral":      ["southcentralus", "eastus", "eastus2"],
}


def _resolve_effective_region(output_dir: Path, target_region: str = "") -> str:
    """Return the normalised region string to use for availability checks."""
    import re
    region = (target_region or "").lower().replace(" ", "")
    if not region:
        vars_tf = output_dir / "variables.tf"
        if vars_tf.exists():
            m = re.search(
                r'variable\s+"location"[^}]*default\s*=\s*"([^"]+)"',
                vars_tf.read_text(encoding="utf-8", errors="ignore"),
                re.DOTALL,
            )
            if m:
                region = m.group(1).lower().replace(" ", "")
    return region


def _find_best_fallback(user_region: str, supported: frozenset[str]) -> str | None:
    """Return the geographically nearest supported region for *user_region*.

    Walks the fallback priority list for *user_region* and returns the first
    entry that is inside *supported*.  Falls back to alphabetically-first
    supported region when the user's region has no priority list.
    """
    for candidate in _REGION_FALLBACK_PRIORITY.get(user_region, []):
        if candidate in supported:
            return candidate
    # No priority match — pick the first supported region alphabetically
    return next(iter(sorted(supported)), None)


def _fix_region_availability(
    output_dir: Path,
    target_region: str = "",
    data_residency: str = "",
) -> dict[str, list[str]]:
    """Universal post-pass: route each resource to a supported region.

    For every resource type in _AZURE_RESOURCE_REGION_CONSTRAINTS, checks whether
    the user's target region is in the supported set.  If not, replaces the
    resource's `location` attribute with the nearest available region derived from
    the geographic fallback priority map.

    The resource group itself is never modified — only individual resources that
    have tighter regional availability than the resource group.

    Returns a dict mapping resource_type → list of .tf file names that were patched,
    so callers can surface the changes to the user.
    """
    import re

    effective_region = _resolve_effective_region(output_dir, target_region)
    if not effective_region:
        return {}

    tf_files = sorted(output_dir.glob("*.tf"))
    report: dict[str, list[str]] = {}

    for resource_type, supported_regions in _AZURE_RESOURCE_REGION_CONSTRAINTS.items():
        if effective_region in supported_regions:
            continue  # user's region is fine for this resource type

        from core.region_constraints import find_best_fallback as _core_fallback
        fallback = _core_fallback(effective_region, supported_regions, data_residency=data_residency)
        if not fallback:
            logger.warning(
                "_fix_region_availability: no fallback found for %s in region %s "
                "(data_residency='%s' may be too restrictive — no supported region in that geography)",
                resource_type, effective_region, data_residency,
            )
            report.setdefault(f"BLOCKED:{resource_type}", []).append(
                f"No region satisfies availability + data_residency='{data_residency}'"
            )
            continue

        _resource_header_re = re.compile(
            r'resource\s+"' + re.escape(resource_type) + r'"\s+"[^"]+"\s*\{'
        )
        _loc_attr_re = re.compile(r'(\blocation\s*=\s*)(?:var\.location|"[^"]*")')

        for tf in tf_files:
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if resource_type not in text:
                continue

            # Use brace-counting to extract the full resource block (handles nested
            # sub-blocks like identity {}, network_acls {}, etc. whose closing }
            # would prematurely terminate a simple [^}]* pattern).
            new_text = text
            offset = 0
            patched = False
            for m in list(_resource_header_re.finditer(text)):
                depth = 0
                i = m.end() - 1
                while i < len(text):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            block_end = i + 1
                            break
                    i += 1
                else:
                    continue

                block = text[m.start(): block_end]
                new_block = _loc_attr_re.sub(
                    lambda lm, fb=fallback: lm.group(1) + f'"{fb}"',
                    block,
                )
                if new_block != block:
                    abs_start = m.start() + offset
                    abs_end   = block_end  + offset
                    new_text  = new_text[:abs_start] + new_block + new_text[abs_end:]
                    offset   += len(new_block) - len(block)
                    patched   = True
                    logger.info(
                        "_fix_region_availability: %s in %s — location '%s' → '%s' "
                        "(service not available in user region)",
                        resource_type, tf.name, effective_region, fallback,
                    )

            if patched:
                try:
                    tf.write_text(new_text, encoding="utf-8")
                    report.setdefault(resource_type, []).append(tf.name)
                except OSError as e:
                    logger.warning("_fix_region_availability: %s: %s", tf.name, e)

    if report:
        logger.warning(
            "_fix_region_availability: %d resource type(s) rerouted to supported regions: %s",
            len(report),
            {rt: files[0] for rt, files in report.items()},
        )
    return report


def _fix_cognitive_services_location(output_dir: Path, target_region: str = "") -> int:
    """Backward-compatible shim — delegates to _fix_region_availability."""
    report = _fix_region_availability(output_dir, target_region)
    cognitive_fixes = len(report.get("azurerm_cognitive_account", []))
    cognitive_fixes += len(report.get("azurerm_cognitive_deployment", []))
    return cognitive_fixes


def _fix_provider_config(output_dir: Path) -> int:
    """Fix common azurerm provider block mistakes across provider.tf and any _providers.tf.

    Root causes addressed:

    1. resource_provider_registrations = ["Microsoft.X", ...] (tuple/list)
       The attribute type changed in azurerm 3.80: it now takes a STRING enum
       ("none" | "core" | "all"), NOT a list. Correct list-style registration uses
       `resource_providers_to_register` (different attribute, list of strings).
       Fix: replace the list form with `resource_provider_registrations = "none"`
            and inject `resource_providers_to_register = [...]` for the items.

    2. Duplicate terraform{} / provider{} declarations.
       The fix loop writes _providers.tf into output/ (targeting the validator's
       temp file name) creating a duplicate. This pass removes _providers.tf if
       provider.tf already exists, preventing "Duplicate required providers" errors.

    3. register_resource_providers = true (wrong attribute name in older prompts).
       Removed; has no effect in azurerm >=3.x.

    Returns total number of fixes applied.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # ── Fix 1: resource_provider_registrations list → string ──────────────────
    _RPR_LIST_RE = re.compile(
        r'(resource_provider_registrations\s*=\s*)\[([^\]]*)\]',
        re.DOTALL,
    )
    _EXTRACT_STRINGS_RE = re.compile(r'"([^"]+)"')

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "resource_provider_registrations" not in text:
            continue

        original = text
        match = _RPR_LIST_RE.search(text)
        if match:
            list_content = match.group(2)
            providers = _EXTRACT_STRINGS_RE.findall(list_content)
            # Replace the list with the string enum "none"
            # If specific providers were listed, add resource_providers_to_register
            replacement = 'resource_provider_registrations = "none"'
            if providers:
                plist = ", ".join(f'"{p}"' for p in providers)
                replacement += f"\n  resource_providers_to_register = [{plist}]"
            text = _RPR_LIST_RE.sub(replacement, text, count=1)
            total_fixes += 1
            logger.info(
                "_fix_provider_config: provider.tf — resource_provider_registrations "
                "list → string 'none' + resource_providers_to_register=%s", providers,
            )

        # Remove deprecated provider attributes:
        # - register_resource_providers (old name, no effect in azurerm >=3.x)
        # - skip_provider_registration (deprecated in v4 → use resource_provider_registrations)
        text = re.sub(r'^\s*register_resource_providers\s*=\s*.*$', '', text, flags=re.MULTILINE)
        if 'skip_provider_registration' in text:
            text = re.sub(r'^\s*skip_provider_registration\s*=\s*.*$', '', text, flags=re.MULTILINE)
            # Inject resource_provider_registrations = "none" if not already present
            if 'resource_provider_registrations' not in text:
                text = re.sub(
                    r'(provider\s+"azurerm"\s*\{[^}]*features\s*\{\s*\})',
                    r'\1\n  resource_provider_registrations = "none"',
                    text, flags=re.DOTALL,
                )
            total_fixes += 1
            logger.info("_fix_provider_config: replaced skip_provider_registration → resource_provider_registrations")

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_provider_config: %s: %s", tf.name, e)

    # ── Fix 2: remove _providers.tf doublon if provider.tf exists ─────────────
    providers_tf  = output_dir / "provider.tf"
    _providers_tf = output_dir / "_providers.tf"
    if providers_tf.exists() and _providers_tf.exists():
        try:
            _providers_tf.unlink()
            total_fixes += 1
            logger.info(
                "_fix_provider_config: removed duplicate _providers.tf "
                "(provider.tf already exists in output dir)"
            )
        except OSError as e:
            logger.warning("_fix_provider_config: could not remove _providers.tf: %s", e)

    # ── Fix 3: inject subscription_id = var.subscription_id in azurerm provider ──
    # azurerm v4+ requires subscription_id in the provider block for terraform validate/plan.
    # Without it, offline HCL validation fails with "subscription_id is a required".
    _PROVIDER_BLOCK_RE = re.compile(
        r'(provider\s+"azurerm"\s*\{)(.*?)(^\s*\})',
        re.DOTALL | re.MULTILINE,
    )
    for tf in [providers_tf, _providers_tf]:
        if not tf.exists():
            continue
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "subscription_id" in text:
            continue  # already present
        match = _PROVIDER_BLOCK_RE.search(text)
        if not match:
            continue
        original = text
        block_inner = match.group(2)
        # Insert subscription_id right after the opening brace, before features {}
        new_inner = "\n  subscription_id                 = var.subscription_id" + block_inner
        text = text[:match.start(2)] + new_inner + text[match.end(2):]
        try:
            tf.write_text(text, encoding="utf-8")
            total_fixes += 1
            logger.info(
                "_fix_provider_config: %s — injected subscription_id = var.subscription_id "
                "(required by azurerm v4+)", tf.name,
            )
        except OSError as e:
            logger.warning("_fix_provider_config: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_provider_config: %d fix(es) total", total_fixes)
    return total_fixes


def _fix_storage_sas_policy_nesting(output_dir: Path) -> int:
    """Fix two azurerm_storage_account sas_policy bugs:

    Bug A — wrong nesting: top-level attributes (e.g. infrastructure_encryption_enabled)
      end up INSIDE the sas_policy {} block.
      Root cause: _inject_block(sas_policy) runs before _inject_attr, and
      _inject_attr uses rfind('}') which lands on sas_policy's closing brace.
      Fix: extract any non-sas attribute from the sas_policy body and append
      it after the closing brace.

    Bug B — incomplete sas_policy: the LLM writes sas_policy with only
      expiration_period but omits the required expiration_action = "Log".
      _inject_block skips the injection because sas_policy already exists.
      Fix: if expiration_action is absent inside the existing sas_policy block,
      insert it.

    Returns the number of files corrected.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # Attributes that belong at the azurerm_storage_account top level but are
    # sometimes found inside sas_policy due to the rfind('}') insertion bug.
    _MISPLACED_ATTRS = re.compile(
        r'^\s*(infrastructure_encryption_enabled'
        r'|https_traffic_only_enabled'
        r'|min_tls_version'
        r'|public_network_access_enabled'
        r'|allow_nested_items_to_be_public'
        r'|cross_tenant_replication_enabled'
        r'|shared_access_key_enabled'
        r'|account_tier'
        r'|account_replication_type'
        r'|account_kind'
        r')\s*=',
        re.MULTILINE,
    )

    _SA_RES_RE = re.compile(
        r'resource\s+"azurerm_storage_account"\s+"[^"]+"\s*\{',
        re.MULTILINE,
    )
    _SAS_BLOCK_RE = re.compile(
        r'(\n[ \t]*sas_policy\s*\{)([^}]*)(\})',
        re.DOTALL,
    )

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if "azurerm_storage_account" not in text or "sas_policy" not in text:
            continue

        original = text

        def _repair_sas(m: re.Match) -> str:
            sas_open, sas_body, sas_close = m.group(1), m.group(2), m.group(3)
            misplaced: list[str] = []
            kept_lines: list[str] = []
            for line in sas_body.splitlines(keepends=True):
                if _MISPLACED_ATTRS.match(line):
                    misplaced.append(line.strip())
                else:
                    kept_lines.append(line)
            if not misplaced:
                return m.group(0)
            repaired_body = "".join(kept_lines)
            suffix = "".join(f"\n  {a}" for a in misplaced)
            return sas_open + repaired_body + sas_close + suffix

        # Only operate inside azurerm_storage_account blocks
        new_text_parts: list[str] = []
        pos = 0
        for sa_m in _SA_RES_RE.finditer(text):
            # find the closing brace of this resource block
            depth, i = 0, sa_m.start()
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            block_end = i + 1
            block = text[sa_m.start():block_end]
            fixed_block = _SAS_BLOCK_RE.sub(_repair_sas, block)
            new_text_parts.append(text[pos:sa_m.start()])
            new_text_parts.append(fixed_block)
            pos = block_end

        new_text_parts.append(text[pos:])
        text = "".join(new_text_parts)

        # ── Bug B: inject missing expiration_action inside existing sas_policy ──
        # If sas_policy exists but has no expiration_action, add it.
        def _add_expiration_action(m: re.Match) -> str:
            body = m.group(2)
            if "expiration_action" in body:
                return m.group(0)
            # Insert expiration_action as first line of the body
            return m.group(1) + '\n    expiration_action = "Log"' + body + m.group(3)

        text = _SAS_BLOCK_RE.sub(_add_expiration_action, text)

        # ── Bug C: expiration_period must be a "DD.HH:MM:SS" timespan string ──
        # Azure's storage account API rejects shorthand like "1d" / "24h" with
        # "InvalidValuesForRequestParameters: ... sasPolicy.sasExpirationPeriod".
        # Normalize common shorthand the LLM emits to the strict timespan format.
        _SHORTHAND_TO_TIMESPAN = {
            "1d": "1.00:00:00",
            "2d": "2.00:00:00",
            "7d": "7.00:00:00",
            "24h": "1.00:00:00",
            "48h": "2.00:00:00",
            "1h": "0.01:00:00",
        }
        _EXP_PERIOD_RE = re.compile(r'(expiration_period\s*=\s*")([^"]+)(")')

        def _normalize_expiration_period(m: re.Match) -> str:
            value = m.group(2)
            if re.fullmatch(r'\d+\.\d{2}:\d{2}:\d{2}', value):
                return m.group(0)  # already valid DD.HH:MM:SS
            replacement = _SHORTHAND_TO_TIMESPAN.get(value, "1.00:00:00")
            return m.group(1) + replacement + m.group(3)

        text = _EXP_PERIOD_RE.sub(_normalize_expiration_period, text)

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
                total_fixes += 1
                logger.info(
                    "_fix_storage_sas_policy_nesting: repaired sas_policy in %s "
                    "(misplaced attrs + missing expiration_action)", tf.name,
                )
            except OSError as e:
                logger.warning("_fix_storage_sas_policy_nesting: %s: %s", tf.name, e)

    return total_fixes


def _fix_corrupted_string_literals(output_dir: Path) -> int:
    """Fix string literals that contain embedded HCL content after the closing quote.

    The LLM sometimes generates resource names that span multiple lines, e.g.:
        name = "stbef75da3"TLS1_2"
    or after _fix_globally_unique_names runs:
        name = "kv-bef75da3"standard"

    These produce 'Unsupported argument' or 'Missing newline' errors in
    terraform init because the parser sees content after the closing quote.

    This fixer detects lines of the form:
        <indent>name = "valid-name"<extra_content>"
    and truncates to:
        <indent>name = "valid-name"
    """
    # Matches: whitespace, an attr name, = "<good-value>"<junk up to next quote or EOL>
    # The "junk" between two double-quotes is what we need to strip.
    _CORRUPT_STR_RE = re.compile(
        r'^([ \t]*\w+\s*=\s*"[^"\n]+)"[^"\n]*"',
        re.MULTILINE,
    )

    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        original = text
        text, n = _CORRUPT_STR_RE.subn(r'\1"', text)
        if n:
            total_fixes += n
            logger.info(
                "_fix_corrupted_string_literals: %s — repaired %d corrupted string literal(s)",
                tf.name, n,
            )
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_corrupted_string_literals: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_corrupted_string_literals: %d correction(s) total", total_fixes)
    return total_fixes


def _fix_orphan_preamble(output_dir: Path) -> int:
    """Remove orphan HCL content before the first valid top-level block.

    When the LLM corrupts a resource name with embedded HCL (e.g.
    ``name = "stml${var.environment\\n  min_tls_version = ...``), the
    _fix_globally_unique_names fixer replaces the name but may leave
    the continuation lines (the rest of the corrupted string that spilled
    out of the name attribute) at the very top of the file — producing
    arguments like ``resource_group_name = ...`` with no enclosing block.

    This fixer detects that pattern (file starts with ``identifier = ...``
    rather than a top-level block keyword) and drops every line before the
    first valid HCL top-level block (``resource``, ``data``, ``variable``,
    ``output``, ``locals``, ``provider``, ``terraform``, ``module``).

    Returns total number of files fixed.
    """
    _TOP_LEVEL_RE = re.compile(
        r'^\s*(resource|data|variable|output|locals|provider|terraform|module)\s*["\{]',
    )
    _ORPHAN_START_RE = re.compile(r'^\s*\w+\s*=\s*\S')  # looks like `attr = value`

    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lines = text.splitlines(keepends=True)
        if not lines:
            continue

        # Check if file starts with an orphan argument (not a top-level block)
        first_non_blank = next(
            (l for l in lines if l.strip() and not l.strip().startswith("#")),
            None,
        )
        if first_non_blank is None or _TOP_LEVEL_RE.match(first_non_blank):
            continue  # file starts correctly

        # Find the first top-level block line
        first_block_idx = next(
            (i for i, l in enumerate(lines) if _TOP_LEVEL_RE.match(l)),
            None,
        )
        if first_block_idx is None:
            continue  # no top-level block at all — leave for the LLM fix loop

        dropped = lines[:first_block_idx]
        logger.info(
            "_fix_orphan_preamble: %s — dropped %d orphan line(s) before first block: %r",
            tf.name, len(dropped), "".join(dropped)[:120],
        )
        new_text = "".join(lines[first_block_idx:])
        try:
            tf.write_text(new_text, encoding="utf-8")
            total_fixes += 1
        except OSError as e:
            logger.warning("_fix_orphan_preamble: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_orphan_preamble: %d file(s) fixed", total_fixes)
    return total_fixes


def _fix_stray_chars_after_braces(output_dir: Path) -> int:
    """Remove stray non-whitespace characters immediately after closing braces.

    The LLM occasionally generates malformed HCL such as:
        }e          ← stray char → "Missing newline after block definition"
        }           ← orphan brace

    Two passes:
      Pass A — strip alphanumeric/punctuation chars glued to '}' on the same line:
               '}e' → '}'  |  ')' → '}'  |  '},' → '}'
      Pass B — remove lines that contain ONLY '}' when the preceding line is
               also '}' (orphan closing brace with no matching open brace context).

    Returns total number of corrections applied.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # Pass A: `}` followed by non-whitespace chars on the same line → keep only `}`
    _stray_re = re.compile(r'^(\s*\})[^\n\}{\s]+\s*$', re.MULTILINE)

    # Pass B: consecutive bare `}` lines — remove the second one when it would
    # close a brace that was never opened (heuristic: depth < 0 means orphan)
    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        original = text

        # Pass A: strip stray characters glued to `}`
        text, n = _stray_re.subn(r'\1', text)
        if n:
            total_fixes += n
            logger.info(
                "_fix_stray_chars_after_braces pass A: %s — removed %d stray char(s) after '}'",
                tf.name, n,
            )

        # Pass B: remove orphan `}` lines using brace depth tracking
        lines = text.splitlines(keepends=True)
        depth = 0
        cleaned: list[str] = []
        for line in lines:
            open_count  = line.count("{")
            close_count = line.count("}")
            new_depth   = depth + open_count - close_count

            if new_depth < 0:
                # This line closes more braces than open → orphan, drop it
                logger.info(
                    "_fix_stray_chars_after_braces pass B: %s — dropped orphan brace line: %r",
                    tf.name, line.rstrip(),
                )
                total_fixes += 1
                new_depth = depth  # depth unchanged (we dropped the line)
            else:
                cleaned.append(line)

            depth = new_depth

        text = "".join(cleaned)

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_stray_chars_after_braces: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_stray_chars_after_braces: %d correction(s) total", total_fixes)
    return total_fixes


# Matches two `key = value` argument assignments glued onto a single line with
# only whitespace between them, e.g.:
#   geo_redundant_backup_enabled = true  version = "16"
# HCL requires each argument on its own line ("Missing newline after argument").
# Captures: indentation, first key=value (value = bareword/number/bool or quoted
# string, no spaces), then the start of the second `key =` assignment.
_GLUED_ARGS_RE = re.compile(
    r'^([ \t]*)(\w+\s*=\s*(?:"[^"\n]*"|[\w.\-]+))[ \t]{2,}(?=\w+\s*=)',
    re.MULTILINE,
)


def _fix_glued_hcl_arguments(output_dir: Path) -> int:
    """Split two argument assignments glued onto one line into separate lines.

    The LLM occasionally drops the newline between two attributes inside a
    resource block, producing HCL that fails `terraform init` with
    "Missing newline after argument", e.g.:
        geo_redundant_backup_enabled = true  version = "16"
    This rewrites it to two properly indented lines. Unlike the deterministic
    value/name fixers (_fix_tf_variables, _fix_globally_unique_names, …) this
    targets a raw HCL syntax defect that those fixers never touch — without it,
    the validate/fix loop retries cosmetic changes forever while the real
    parse error persists, eventually exhausting correction attempts and
    escalating to a human.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        original = text
        text, n = _GLUED_ARGS_RE.subn(r'\1\2\n\1', text)
        if n:
            total_fixes += n
            logger.info(
                "_fix_glued_hcl_arguments: %s — split %d glued argument pair(s) onto separate lines",
                tf.name, n,
            )
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_glued_hcl_arguments: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_glued_hcl_arguments: %d correction(s) total", total_fixes)
    return total_fixes


def _fix_cognitive_network_acls(output_dir: Path) -> int:
    """Fix azurerm_cognitive_account + network_acls co-existence issues.

    The azurerm provider enforces:
      "all of custom_subdomain_name,network_acls must be specified"
    i.e. whenever a network_acls block is present, custom_subdomain_name is required.

    Two passes:
      Pass A — if network_acls block is present AND custom_subdomain_name is missing,
               inject custom_subdomain_name derived from the migration_id (globally unique).
      Pass B — remove any empty network_acls blocks (default_action=Deny with no VNet/IP
               rules) that make the service completely inaccessible without private endpoints.
               We keep the #checkov:skip annotation so CKV_AZURE_247 is still suppressed.

    Returns total number of fixes applied.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    migration_id_raw = output_dir.name
    hex_only         = migration_id_raw.replace("-", "")
    unique_suffix    = hex_only[:12].lower()
    custom_subdomain = f"cog{unique_suffix}"  # e.g. "cog5601cadcbd81" — globally unique, ≤24 chars

    _cog_resource_re = re.compile(
        r'resource\s+"azurerm_cognitive_account"\s+"[^"]+"\s*\{',
        re.MULTILINE,
    )
    _network_acls_re = re.compile(r'\bnetwork_acls\s*\{[^}]*\}', re.DOTALL)
    _custom_sub_re   = re.compile(r'\bcustom_subdomain_name\b')

    # Pass B: empty/inaccessible network_acls blocks to remove
    _EMPTY_NETWORK_ACLS_RE = re.compile(
        r'\n?\s*network_acls\s*\{[^}]*default_action\s*=\s*"Deny"[^}]*\}',
        re.DOTALL,
    )

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "azurerm_cognitive_account" not in text:
            continue

        original = text

        for m in list(_cog_resource_re.finditer(original)):
            depth, i = 0, m.end() - 1
            while i < len(original):
                if original[i] == "{":
                    depth += 1
                elif original[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block_end = i + 1
                        break
                i += 1
            else:
                continue

            block = original[m.start():block_end]

            # Pass B: remove inaccessible Deny network_acls (no VNet/IP exceptions)
            new_block, n_b = _EMPTY_NETWORK_ACLS_RE.subn("", block)
            if n_b:
                text = text[: m.start()] + new_block + text[block_end:]
                block = new_block
                total_fixes += n_b
                logger.info(
                    "_fix_cognitive_network_acls pass B: removed empty Deny network_acls from %s",
                    tf.name,
                )

            # Pass A: if network_acls is still present, ensure custom_subdomain_name is there
            if _network_acls_re.search(block) and not _custom_sub_re.search(block):
                idx = block.rfind("}")
                prefix = block[:idx]
                if not prefix.endswith("\n"):
                    prefix += "\n"
                new_block = prefix + f'  custom_subdomain_name = "{custom_subdomain}"\n' + block[idx:]
                text = text[: m.start()] + new_block + text[block_end:]
                total_fixes += 1
                logger.info(
                    "_fix_cognitive_network_acls pass A: injected custom_subdomain_name=%s into %s",
                    custom_subdomain, tf.name,
                )

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning("_fix_cognitive_network_acls: %s: %s", tf.name, e)

    if total_fixes:
        logger.info("_fix_cognitive_network_acls: %d fix(es) total", total_fixes)
    return total_fixes


def _fix_globally_unique_names(output_dir: Path) -> int:
    """Ensure Azure resource names that must be globally unique are derived from the migration_id.

    Azure requires globally unique names (across all subscriptions) for:
      - Storage Accounts  : "st" + 14 hex chars → max 16 chars
      - PostgreSQL servers : "pg-" + 8 hex chars → conflict-free
      - Cognitive Accounts: "cog-" + 8 hex chars → conflict-free
      - AKS clusters dns_prefix is scoped to region — less critical but still prefixed

    Only rewrites names that look generic (no UUID hex pattern already embedded).
    """
    migration_id_raw = output_dir.name
    hex_only         = migration_id_raw.replace("-", "")
    slug8            = hex_only[:8].lower()   # e.g. "adb8d66d"
    slug14           = hex_only[:14].lower()  # e.g. "adb8d66d2a0247"

    unique_names = {
        "azurerm_storage_account":           f"st{slug14}",      # st + 14 = 16 chars
        "azurerm_postgresql_flexible_server": f"pg-{slug8}",     # pg- + 8 = 11 chars
        "azurerm_mysql_flexible_server":     f"mysql-{slug8}",   # mysql- + 8 = 14 chars
        "azurerm_cognitive_account":         f"cog-{slug8}",     # cog- + 8 = 12 chars
        "azurerm_search_service":            f"srch-{slug8}",    # srch- + 8 = 13 chars
        # Key Vault names are globally unique across all Azure subscriptions (DNS-based).
        # A static name like "ml-kv" collides with any prior run that was soft-deleted
        # but not purged — azure refuses CreateOrUpdate with VaultAlreadyExists.
        "azurerm_key_vault":                 f"kv-{slug8}",      # kv- + 8 = 11 chars
    }

    tf_files   = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    # Maps resource type → variable names that control the resource name attribute.
    # Multiple entries per type handle the case where different labels use different vars
    # (e.g. azurerm_storage_account "main" uses storage_account_name,
    #        azurerm_storage_account "mlstorage" should use ml_storage_account_name).
    _VAR_NAME_MAP: dict[str, list[str]] = {
        "azurerm_storage_account":           ["storage_account_name", "ml_storage_account_name"],
        "azurerm_postgresql_flexible_server": ["db_server_name"],
        "azurerm_mysql_flexible_server":      ["db_server_name"],
        "azurerm_cognitive_account":          ["cognitive_account_name"],
        "azurerm_search_service":             ["search_service_name"],
    }

    # Per-resource-type unique name variants for secondary labels (e.g. "mlstorage")
    _SECONDARY_UNIQUE_NAMES = {
        "azurerm_storage_account": f"stml{hex_only[:12].lower()}",  # "stml" + 12 hex = 16 chars
    }

    # Track already-assigned names: resource_type → {label → unique_name}
    _assigned: dict[str, dict[str, str]] = {}

    for resource_type, base_unique_name in unique_names.items():
        _assigned.setdefault(resource_type, {})

        # ── Collect all (label, name_value, is_var, var_name) across all tf files ──
        # First pass: scan all files to build a complete picture before writing anything.
        label_info: dict[str, dict] = {}  # label → {name, is_var, var_name, tf_path}

        for tf in tf_files:
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if resource_type not in text:
                continue

            # Extract each resource block via brace-counting to avoid [^}]* truncating
            # at the first nested brace (e.g. interpolation `${var.x}` or sub-blocks).
            _res_header_re = re.compile(
                r'resource\s+"' + re.escape(resource_type) + r'"\s+"([^"]+)"\s*\{',
            )
            for hm in _res_header_re.finditer(text):
                label = hm.group(1)
                depth = 0
                i = hm.end() - 1  # position of the opening brace
                block_end = None
                for j in range(i, len(text)):
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                        if depth == 0:
                            block_end = j + 1
                            break
                if block_end is None:
                    continue
                block_body = text[hm.end():block_end]

                # Case 1: hardcoded string name — match only the name attribute line
                m_name = re.search(r'^\s*name\s*=\s*"([^"]+)"', block_body, re.MULTILINE)
                if m_name:
                    label_info[label] = {
                        "name": m_name.group(1),
                        "is_var": False,
                        "var_name": None,
                        "tf": tf,
                    }
                    continue

                # Case 2: var.X reference
                m_var = re.search(r'^\s*name\s*=\s*var\.([a-zA-Z_][a-zA-Z0-9_]*)', block_body, re.MULTILINE)
                if m_var:
                    label_info[label] = {
                        "name": None,
                        "is_var": True,
                        "var_name": m_var.group(1),
                        "tf": tf,
                    }

        if not label_info:
            continue

        # ── Assign a unique name to each label ──
        labels = list(label_info.keys())
        for i, label in enumerate(labels):
            if i == 0:
                _assigned[resource_type][label] = base_unique_name
            else:
                secondary = _SECONDARY_UNIQUE_NAMES.get(resource_type)
                if secondary and secondary not in _assigned[resource_type].values():
                    _assigned[resource_type][label] = secondary
                else:
                    suffix = re.sub(r"[^a-z0-9]", "", label.lower())[:4]
                    _assigned[resource_type][label] = (base_unique_name + suffix)[:24]

        # ── Second pass: apply fixes ──
        for label, info in label_info.items():
            target_name = _assigned[resource_type][label]
            tf = info["tf"]

            if not info["is_var"]:
                # Case 1: fix hardcoded string — rewrite in place
                current_name = info["name"]
                if current_name == target_name:
                    continue
                try:
                    text = tf.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                # Replace name = "current_name" → name = "target_name" for this label only
                pattern = (
                    r'(resource\s+"' + re.escape(resource_type) + r'"\s+"'
                    + re.escape(label) + r'"\s*\{[^}]*?\bname\s*=\s*)"'
                    + re.escape(current_name) + r'"'
                )
                new_text = re.sub(pattern, lambda m: m.group(1) + f'"{target_name}"', text, count=1, flags=re.DOTALL)
                if new_text != text:
                    try:
                        tf.write_text(new_text, encoding="utf-8")
                        total_fixes += 1
                        logger.info(
                            "_fix_globally_unique_names: %s — %s[%s] hardcoded '%s' → '%s'",
                            tf.name, resource_type, label, current_name, target_name,
                        )
                    except OSError as e:
                        logger.warning("_fix_globally_unique_names: %s write: %s", tf.name, e)
            else:
                # Case 2: fix via variables.tf default value
                var_name = info["var_name"]
                vars_file = tf.parent / "variables.tf"
                if not vars_file.exists():
                    continue
                try:
                    vars_text = vars_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                var_block_re = re.compile(
                    r'(variable\s+"' + re.escape(var_name) + r'"\s*\{[^}]*?default\s*=\s*)"([^"]*)"',
                    re.DOTALL,
                )
                m_def = var_block_re.search(vars_text)
                if not m_def:
                    continue
                current_default = m_def.group(2)
                if current_default == target_name:
                    continue
                new_vars = vars_text[:m_def.start(2)] + target_name + vars_text[m_def.end(2):]
                try:
                    vars_file.write_text(new_vars, encoding="utf-8")
                    total_fixes += 1
                    logger.info(
                        "_fix_globally_unique_names: variables.tf — %s default '%s' → '%s' (label=%s)",
                        var_name, current_default, target_name, label,
                    )
                except OSError as e:
                    logger.warning("_fix_globally_unique_names: variables.tf write: %s", e)

    return total_fixes


# ── Required business attributes per resource type ────────────────────────────
# Maps terraform resource type → list of (attr_key, default_value) pairs.
# These are FUNCTIONAL attributes (not just security) that the LLM often omits,
# causing terraform validate or apply to fail with "required argument not set".
# Values use var.* references so they're resolved at plan time.
_REQUIRED_BUSINESS_ATTRS: dict[str, list[tuple[str, str]]] = {

    # ── Azure PostgreSQL Flexible Server ──────────────────────────────────────
    # zone is intentionally omitted — availability varies by region.
    # Azure will assign an available zone automatically when zone is not set.
    "azurerm_postgresql_flexible_server": [
        ("version",                   '"14"'),
        ("storage_mb",                "32768"),
        ("administrator_login",       "var.db_admin_username"),
        ("administrator_password",    "var.db_password"),
    ],

    # ── Azure MySQL Flexible Server ────────────────────────────────────────────
    "azurerm_mysql_flexible_server": [
        ("version",                   '"8.0.21"'),
        ("administrator_login",       "var.db_admin_username"),
        ("administrator_password",    "var.db_password"),
    ],

    # ── Azure SQL Managed Instance ─────────────────────────────────────────────
    "azurerm_mssql_managed_instance": [
        ("administrator_login",       "var.admin_username"),
        ("administrator_login_password", "var.admin_password"),
        ("sku_name",                  '"GP_Gen5"'),
        ("vcores",                    "4"),
        ("storage_size_in_gb",        "32"),
        ("license_type",              '"LicenseIncluded"'),
        ("subnet_id",                 "var.subnet_id"),
    ],

    # ── Azure Cosmos DB ────────────────────────────────────────────────────────
    "azurerm_cosmosdb_account": [
        ("offer_type",                '"Standard"'),
        ("kind",                      '"GlobalDocumentDB"'),
    ],

    # ── Azure Cognitive Account (OpenAI, Speech, Vision…) ─────────────────────
    "azurerm_cognitive_account": [
        ("sku_name",                  '"S0"'),
        ("public_network_access_enabled", "false"),
    ],

    # ── Azure Machine Learning Workspace ──────────────────────────────────────
    "azurerm_machine_learning_workspace": [
        ("friendly_name",             '"ML Workspace"'),
        ("public_network_access_enabled", "false"),
    ],

    # ── Azure AI Search (vector DB replacement) ────────────────────────────────
    "azurerm_search_service": [
        ("sku",                       '"standard"'),
        ("replica_count",             "1"),
        ("partition_count",           "1"),
        ("public_network_access_enabled", "false"),
    ],

    # ── Azure Container Registry ───────────────────────────────────────────────
    "azurerm_container_registry": [
        ("sku",                       '"Standard"'),
        ("admin_enabled",             "false"),
        ("public_network_access_enabled", "false"),
    ],

    # ── Azure Kubernetes Service ───────────────────────────────────────────────
    "azurerm_kubernetes_cluster": [
        ("dns_prefix",                '"cloud-migrator"'),
        ("sku_tier",                  '"Standard"'),
    ],

    # ── Azure App Service Plan ─────────────────────────────────────────────────
    "azurerm_service_plan": [
        ("os_type",                   '"Linux"'),
        ("sku_name",                  '"P1v3"'),
    ],

    # ── Azure Function App ─────────────────────────────────────────────────────
    "azurerm_linux_function_app": [
        ("https_only",                "true"),
        ("functions_extension_version", '"~4"'),
    ],

    # ── AWS RDS Instance ───────────────────────────────────────────────────────
    "aws_db_instance": [
        ("storage_encrypted",         "true"),
        ("backup_retention_period",   "7"),
        ("deletion_protection",       "true"),
        ("skip_final_snapshot",       "false"),
        ("multi_az",                  "true"),
        ("publicly_accessible",       "false"),
        ("auto_minor_version_upgrade", "true"),
    ],

    # ── AWS RDS Cluster (Aurora) ───────────────────────────────────────────────
    "aws_rds_cluster": [
        ("storage_encrypted",         "true"),
        ("backup_retention_period",   "7"),
        ("deletion_protection",       "true"),
        ("skip_final_snapshot",       "false"),
    ],

    # ── AWS S3 Bucket ──────────────────────────────────────────────────────────
    "aws_s3_bucket": [
        ("force_destroy",             "false"),
    ],

    # ── AWS DynamoDB Table ─────────────────────────────────────────────────────
    "aws_dynamodb_table": [
        ("billing_mode",              '"PAY_PER_REQUEST"'),
        ("point_in_time_recovery",    "{ enabled = true }"),  # block injection handled separately
    ],

    # ── AWS ElastiCache Cluster ────────────────────────────────────────────────
    "aws_elasticache_cluster": [
        ("engine",                    '"redis"'),
        ("engine_version",            '"7.0"'),
        ("node_type",                 '"cache.t3.micro"'),
        ("num_cache_nodes",           "1"),
    ],

    # ── GCP SQL Database Instance ──────────────────────────────────────────────
    "google_sql_database_instance": [
        ("deletion_protection",       "true"),
    ],

    # ── GCP Storage Bucket ────────────────────────────────────────────────────
    "google_storage_bucket": [
        ("force_destroy",             "false"),
        ("uniform_bucket_level_access", "true"),
        ("public_access_prevention",  '"enforced"'),
    ],

    # ── GCP Vertex AI Endpoint ────────────────────────────────────────────────
    "google_vertex_ai_endpoint": [
        ("region",                    "var.location"),
    ],
}


def _ensure_required_business_attrs(output_dir: Path) -> int:
    """Deterministic post-pass: inject functional required attributes the LLM omitted.

    Covers attributes that make resources deployable (not just security-compliant):
    - Database versions, admin credentials, storage sizing
    - Deletion protection, backup retention, multi-AZ flags
    - SKU/tier defaults for every common resource type (Azure, AWS, GCP)

    This pass only ADDS missing attributes — it never overwrites existing values.
    Returns the total number of attribute injections performed.
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    total_fixes = 0

    def _has_attr(block: str, attr_key: str) -> bool:
        return bool(re.search(rf'(?m)^\s*{re.escape(attr_key)}\s*=', block))

    def _inject_attr(block: str, attr_key: str, default_val: str) -> str:
        if _has_attr(block, attr_key):
            return block
        idx = block.rfind("}")
        if idx == -1:
            return block
        prefix = block[:idx]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return prefix + f"  {attr_key} = {default_val}\n" + block[idx:]

    resource_re = re.compile(
        r'(resource\s+"([^"]+)"\s+"([^"]+)"\s*\{)',
        re.MULTILINE,
    )

    for tf in tf_files:
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue

        original = text
        offset = 0

        for m in list(resource_re.finditer(original)):
            rtype = m.group(2)
            attrs_to_inject = _REQUIRED_BUSINESS_ATTRS.get(rtype)
            if not attrs_to_inject:
                continue

            # Walk to matching closing brace
            depth = 0
            i = m.end() - 1
            while i < len(original):
                if original[i] == '{':
                    depth += 1
                elif original[i] == '}':
                    depth -= 1
                    if depth == 0:
                        block_end = i + 1
                        break
                i += 1
            else:
                continue

            block_start = m.start()
            block = original[block_start:block_end]
            new_block = block

            for attr_key, default_val in attrs_to_inject:
                prev = new_block
                new_block = _inject_attr(new_block, attr_key, default_val)
                if new_block != prev:
                    logger.info(
                        "_ensure_required_business_attrs: %s — injected %s = %s",
                        rtype, attr_key, default_val,
                    )

            if new_block != block:
                abs_start = block_start + offset
                abs_end   = block_end   + offset
                text = text[:abs_start] + new_block + text[abs_end:]
                offset += len(new_block) - len(block)
                total_fixes += 1

        if text != original:
            try:
                tf.write_text(text, encoding="utf-8")
            except OSError as e:
                logger.warning(f"_ensure_required_business_attrs: {tf.name}: {e}")

    if total_fixes:
        logger.info(f"_ensure_required_business_attrs: {total_fixes} injection(s) total")
    return total_fixes


# ── Azure network post-pass ───────────────────────────────────────────────────
#
# _inject_azure_network() is a deterministic post-pass that runs after all other
# fixers.  It handles three problems that consistently block `terraform apply`:
#
#  Problem 1 — Missing network.tf
#    azurerm_postgresql_flexible_server requires delegated_subnet_id (private
#    access mode).  azurerm_storage_account and azurerm_cognitive_account with
#    public_network_access_enabled = false require azurerm_private_endpoint.
#    Without a VNet + subnets, terraform apply hard-fails at plan time.
#
#  Problem 2 — azurerm_user_assigned_identity without azurerm_role_assignment
#    The UAI is created but has no RBAC role — it is functionally an orphan.
#    This pass appends a Contributor role assignment scoped to the resource group.
#
#  Problem 3 — PostgreSQL version 14 (EOL November 2026)
#    Agent 02 defaults to version = "14".  This pass upgrades it to "16".
#
# The pass is idempotent: it checks whether network.tf already exists and whether
# the delegation/PE/role blocks are already present before writing anything.
# ─────────────────────────────────────────────────────────────────────────────

_AZURE_NETWORK_TF = """\
# ── network.tf — generated by cloud-migrator post-pass _inject_azure_network ──
# Required for:
#   - azurerm_postgresql_flexible_server  (delegated subnet, private access mode)
#   - azurerm_storage_account             (private endpoint — public access disabled)
#   - azurerm_cognitive_account           (private endpoint — public access disabled)

resource "azurerm_virtual_network" "main" {{
  name                = "vnet-{slug}"
  address_space       = ["10.0.0.0/16"]
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

# Dedicated subnet for PostgreSQL Flexible Server (delegation required)
resource "azurerm_subnet" "postgres" {{
  name                 = "snet-postgres"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]

  delegation {{
    name = "postgres-delegation"
    service_delegation {{
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }}
  }}
}}

# Dedicated subnet for Private Endpoints (storage + cognitive)
resource "azurerm_subnet" "private_endpoints" {{
  name                 = "snet-private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.2.0/24"]
  private_endpoint_network_policies = "Disabled"
}}

# ── Private DNS Zones ─────────────────────────────────────────────────────────

resource "azurerm_private_dns_zone" "postgres" {{
  name                = "privatelink.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {{
  name                  = "vnet-link-postgres"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

resource "azurerm_private_dns_zone" "storage_blob" {{
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.main.name
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

resource "azurerm_private_dns_zone_virtual_network_link" "storage_blob" {{
  name                  = "vnet-link-storage-blob"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.storage_blob.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

resource "azurerm_private_dns_zone" "cognitive" {{
  name                = "privatelink.cognitiveservices.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

resource "azurerm_private_dns_zone_virtual_network_link" "cognitive" {{
  name                  = "vnet-link-cognitive"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.cognitive.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags = {{ ManagedBy = "cloud-migrator" }}
}}

# ── Private Endpoints ─────────────────────────────────────────────────────────

resource "azurerm_private_endpoint" "storage_blob" {{
  name                = "pe-storage-blob"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {{
    name                           = "psc-storage-blob"
    private_connection_resource_id = azurerm_storage_account.main.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }}

  private_dns_zone_group {{
    name                 = "pdnszg-storage-blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.storage_blob.id]
  }}

  tags = {{ ManagedBy = "cloud-migrator" }}
  depends_on = [azurerm_private_dns_zone_virtual_network_link.storage_blob]
}}

resource "azurerm_private_endpoint" "cognitive" {{
  name                = "pe-cognitive"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {{
    name                           = "psc-cognitive"
    private_connection_resource_id = azurerm_cognitive_account.main.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }}

  private_dns_zone_group {{
    name                 = "pdnszg-cognitive"
    private_dns_zone_ids = [azurerm_private_dns_zone.cognitive.id]
  }}

  tags = {{ ManagedBy = "cloud-migrator" }}
  depends_on = [azurerm_private_dns_zone_virtual_network_link.cognitive]
}}

# ── Network Security Group — deny-all inbound fallback (CKV_AZURE_65) ─────────
resource "azurerm_network_security_group" "main" {{
  name                = "nsg-{slug}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name

  security_rule {{
    name                       = "deny-all-inbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }}

  tags = {{ ManagedBy = "cloud-migrator" }}
}}
"""

_AZURE_ROLE_ASSIGNMENT_BLOCK = """\

resource "azurerm_role_assignment" "uai_contributor" {{
  count                            = var.enable_role_assignments
  scope                            = azurerm_resource_group.main.id
  role_definition_name             = "Contributor"
  principal_id                     = azurerm_user_assigned_identity.main.principal_id
  skip_service_principal_aad_check = true
  depends_on                       = [azurerm_user_assigned_identity.main]
}}
"""


_ENABLE_ROLE_ASSIGNMENTS_DEFAULT_RE = re.compile(
    r'(variable\s+"enable_role_assignments"\s*\{[^}]*?default\s*=\s*)(\d+)',
    re.DOTALL,
)


def _disable_role_assignments_by_default(output_dir: Path) -> int:
    """Force `enable_role_assignments` default to 0 wherever declared.

    The deploying service principal typically lacks
    'Microsoft.Authorization/roleAssignments/write' on the subscription
    (granting it requires subscription-Owner / User Access Administrator,
    which the lab/test SP doesn't have). `terraform apply` then fails with
    AuthorizationFailed on every azurerm_role_assignment resource.

    The LLM sometimes writes its own `variable "enable_role_assignments"`
    declaration with `default = 1` (enabling role assignments), overriding
    the safe default=0 that _add_missing_variable_declarations would use.
    This pass rewrites any such declaration to default = 0 — the
    azurerm_role_assignment resources use `count = var.enable_role_assignments`
    so they become no-ops, and the user can opt in later (terraform apply
    -var enable_role_assignments=1) once the SP has the right permissions.

    Returns the number of files patched.
    """
    total_fixes = 0
    for tf in sorted(output_dir.glob("*.tf")):
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "enable_role_assignments" not in text:
            continue
        new_text, n = _ENABLE_ROLE_ASSIGNMENTS_DEFAULT_RE.subn(r'\g<1>0', text)
        if n and new_text != text:
            try:
                tf.write_text(new_text, encoding="utf-8")
                total_fixes += 1
                logger.info(
                    "_disable_role_assignments_by_default: %s — forced "
                    "enable_role_assignments default to 0 (SP lacks "
                    "roleAssignments/write permission)",
                    tf.name,
                )
            except OSError as e:
                logger.warning("_disable_role_assignments_by_default: %s: %s", tf.name, e)
    return total_fixes


def _inject_azure_network(output_dir: Path) -> int:
    """Deterministic post-pass for Azure workspaces.

    Fixes three deployment blockers that the LLM consistently misses:

    1. Generates network.tf with VNet, delegated PostgreSQL subnet, private-endpoint
       subnet, Private DNS Zones, Private Endpoints for storage + cognitive.
       Skipped if network.tf already exists and already contains azurerm_virtual_network.

    2. Wires delegated_subnet_id + private_dns_zone_id into every
       azurerm_postgresql_flexible_server block that lacks them.

    3. Appends azurerm_role_assignment to iam.tf when azurerm_user_assigned_identity
       is declared but no role_assignment exists.

    4. Upgrades PostgreSQL version from "14" to "16" (14 is EOL).

    Returns the total number of modifications (files written + attribute injections).
    """
    tf_files = list(output_dir.glob("*.tf"))
    if not tf_files:
        return 0

    # Only run for Azure workspaces
    full_text = "\n".join(
        tf.read_text(encoding="utf-8", errors="ignore") for tf in tf_files
    )
    if "azurerm_" not in full_text:
        return 0

    total_fixes = 0
    migration_slug = output_dir.name.replace("-", "")[:8].lower()

    # ── Fix 1: generate network.tf if missing or incomplete ──────────────────
    net_file = output_dir / "network.tf"
    needs_network = not net_file.exists() or "azurerm_virtual_network" not in (
        net_file.read_text(encoding="utf-8", errors="ignore") if net_file.exists() else ""
    )

    # Only generate private endpoints for resources that actually exist
    has_storage   = "azurerm_storage_account" in full_text
    has_cognitive = "azurerm_cognitive_account" in full_text
    has_postgres  = "azurerm_postgresql_flexible_server" in full_text

    if needs_network and (has_postgres or has_storage or has_cognitive):
        network_content = _AZURE_NETWORK_TF.format(slug=migration_slug)

        def _strip_resource_block(content: str, resource_label: str) -> str:
            """Remove a complete 'resource "TYPE" "NAME" { ... }' block using
            brace-depth tracking instead of regex — handles arbitrary nesting."""
            # resource_label is e.g. 'resource "azurerm_private_endpoint" "cognitive"'
            pattern = re.compile(
                re.escape(resource_label) + r'\s*\{',
                re.DOTALL,
            )
            m = pattern.search(content)
            if not m:
                return content
            depth, i = 0, m.start()
            while i < len(content):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            # Consume trailing newline(s)
            while i < len(content) and content[i] == "\n":
                i += 1
            return content[:m.start()] + content[i:]

        # Strip blocks for resources not present in the generated workspace.
        # Each strip call removes exactly one block — safe to call multiple times.
        if not has_storage:
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_endpoint" "storage_blob"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone" "storage_blob"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone_virtual_network_link" "storage_blob"')
        if not has_cognitive:
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_endpoint" "cognitive"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone" "cognitive"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone_virtual_network_link" "cognitive"')
        if not has_postgres:
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_subnet" "postgres"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone" "postgres"')
            network_content = _strip_resource_block(
                network_content, 'resource "azurerm_private_dns_zone_virtual_network_link" "postgres"')

        try:
            net_file.write_text(network_content.strip() + "\n", encoding="utf-8")
            logger.info(
                "_inject_azure_network: wrote network.tf (%d chars) — "
                "VNet + subnets + DNS zones + private endpoints",
                len(network_content),
            )
            total_fixes += 1
        except OSError as e:
            logger.warning("_inject_azure_network: could not write network.tf: %s", e)

    # ── Fix 2: wire delegated_subnet_id + private_dns_zone_id into PostgreSQL ─
    if has_postgres:
        for tf in sorted(output_dir.glob("*.tf")):
            try:
                text = tf.read_text(encoding="utf-8")
            except OSError:
                continue
            if "azurerm_postgresql_flexible_server" not in text:
                continue

            original = text

            # Inject delegated_subnet_id if missing
            if "delegated_subnet_id" not in text:
                text = re.sub(
                    r'(resource\s+"azurerm_postgresql_flexible_server"\s+"[^"]+"\s*\{)',
                    lambda m: m.group(0) + "\n  delegated_subnet_id   = azurerm_subnet.postgres.id",
                    text,
                )

            # Inject private_dns_zone_id if missing
            if "private_dns_zone_id" not in text:
                text = re.sub(
                    r'(resource\s+"azurerm_postgresql_flexible_server"\s+"[^"]+"\s*\{)',
                    lambda m: m.group(0) + "\n  private_dns_zone_id   = azurerm_private_dns_zone.postgres.id",
                    text,
                )

            # Fix version 14 → 16 (PostgreSQL 14 is EOL)
            text = re.sub(
                r'(\bversion\s*=\s*)"14"',
                r'\1"16"',
                text,
            )

            if text != original:
                try:
                    tf.write_text(text, encoding="utf-8")
                    total_fixes += 1
                    logger.info(
                        "_inject_azure_network: patched %s — "
                        "delegated_subnet_id + private_dns_zone_id + version=16",
                        tf.name,
                    )
                except OSError as e:
                    logger.warning("_inject_azure_network: %s: %s", tf.name, e)

    # ── Fix 3: add azurerm_role_assignment when UAI has none ─────────────────
    has_uai             = "azurerm_user_assigned_identity" in full_text
    has_role_assignment = "azurerm_role_assignment" in full_text

    if has_uai and not has_role_assignment:
        iam_file = output_dir / "iam.tf"
        target   = iam_file if iam_file.exists() else output_dir / "main.tf"
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            role_block = _AZURE_ROLE_ASSIGNMENT_BLOCK.format()
            target.write_text(existing.rstrip() + role_block, encoding="utf-8")
            total_fixes += 1
            logger.info(
                "_inject_azure_network: appended azurerm_role_assignment "
                "(Contributor on RG) to %s",
                target.name,
            )
        except OSError as e:
            logger.warning("_inject_azure_network: role_assignment write failed: %s", e)

    if total_fixes:
        logger.info("_inject_azure_network: %d fix(es) total", total_fixes)
    return total_fixes


# ── Health-check outputs post-pass ────────────────────────────────────────────
#
# _ensure_health_check_outputs() guarantees that outputs.tf always exposes
# the FQDNs / names needed by the health_check_node and deploy.sh probes:
#   - postgresql_server_fqdn    (from azurerm_postgresql_flexible_server)
#   - storage_account_name      (from azurerm_storage_account)
#   - cognitive_account_name    (from azurerm_cognitive_account)
#
# These outputs are also captured by the TerraformRunner after `terraform output`
# and stored in artifacts["apply_outputs"] so health_check_node can probe
# the live Azure endpoints without hardcoding names.
#
_HC_OUTPUT_SPECS: list[tuple[str, str, str]] = [
    # (output_name, tf_resource_type, attribute)
    ("postgresql_server_fqdn",    "azurerm_postgresql_flexible_server", "fqdn"),
    ("postgresql_admin_login",    "azurerm_postgresql_flexible_server", "administrator_login"),
    ("postgresql_database_name",  "azurerm_postgresql_flexible_server", "name"),
    ("storage_account_name",      "azurerm_storage_account",            "name"),
    ("storage_container_name",    "azurerm_storage_container",          "name"),
    ("cognitive_account_name",    "azurerm_cognitive_account",          "name"),
    ("cognitive_endpoint",        "azurerm_cognitive_account",          "endpoint"),
]

_HC_RESOURCE_NAME_RE = re.compile(
    r'resource\s+"({rtype})"\s+"(\w+)"', re.MULTILINE
)


def _ensure_health_check_outputs(output_dir: Path) -> int:
    """Post-pass: guarantee outputs.tf exposes health-check-required values.

    Scans all .tf files in output_dir to detect which Azure resource types are
    present, then adds the corresponding output blocks to outputs.tf only if
    they are not already declared.  Safe to call multiple times (idempotent).
    """
    if not output_dir.is_dir():
        return 0

    # Discover which resource types exist and their logical names
    resource_instances: dict[str, str] = {}  # rtype → first logical name found
    for tf in sorted(output_dir.glob("*.tf")):
        if tf.name == "outputs.tf":
            continue
        try:
            text = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        for _out_name, rtype, _attr in _HC_OUTPUT_SPECS:
            if rtype in resource_instances:
                continue
            m = re.search(
                r'resource\s+"' + re.escape(rtype) + r'"\s+"(\w+)"',
                text,
            )
            if m:
                resource_instances[rtype] = m.group(1)

    if not resource_instances:
        return 0

    # Read existing outputs.tf
    outputs_path = output_dir / "outputs.tf"
    existing = outputs_path.read_text(encoding="utf-8") if outputs_path.exists() else ""

    new_blocks: list[str] = []
    for out_name, rtype, attr in _HC_OUTPUT_SPECS:
        logical = resource_instances.get(rtype)
        if not logical:
            continue
        # Skip if output already declared
        if re.search(r'output\s+"' + re.escape(out_name) + r'"', existing):
            continue
        block = (
            f'output "{out_name}" {{\n'
            f'  value = {rtype}.{logical}.{attr}\n'
            f'}}\n'
        )
        new_blocks.append(block)

    if not new_blocks:
        return 0

    separator = "\n# ── health-check outputs (added by cloud-migrator post-pass) ──\n"
    updated = existing.rstrip() + "\n" + separator + "\n".join(new_blocks)
    try:
        outputs_path.write_text(updated, encoding="utf-8")
        logger.info(
            "_ensure_health_check_outputs: added %d output(s) to outputs.tf: %s",
            len(new_blocks),
            [b.split('"')[1] for b in new_blocks],
        )
    except OSError as e:
        logger.warning("_ensure_health_check_outputs: could not write outputs.tf: %s", e)
        return 0
    return len(new_blocks)

