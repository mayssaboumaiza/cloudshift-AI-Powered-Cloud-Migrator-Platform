"""
security_policy_engine.py — Pre-generation behavioral security constraint layer.

Responsibilities:
  1. Inject structural/behavioral hints into the generation prompt BEFORE the LLM runs.
     Examples: "add an identity {} block", "generate companion S3 versioning resource".
  2. Classify Checkov failures by security category (used by fix_targeted loop).

Attribute-value enforcement (TLS version, public_network_access_enabled, etc.) is
handled post-generation by checkov_fixer.py — not here.  SPE is Category B only
(missing structure), checkov_fixer handles Category A (wrong attribute value).

Priority:
  SecurityPolicyEngine behavioral hints → LLM generates → checkov_fixer attribute fixes
  → validate_iac (MCP terraform validate + checkov)
"""
from __future__ import annotations

import logging

logger = logging.getLogger("SecurityPolicyEngine")

# ─────────────────────────────────────────────────────────────────────────────
# Behavioral constraints per Terraform resource type.
#
# Keys starting with "_requires_" or "_no_" are structural requirements —
# the LLM must generate the described block/resource, not just set an attribute.
# checkov_fixer.py handles all plain attribute→value enforcement instead.
# ─────────────────────────────────────────────────────────────────────────────
SECURITY_RULES: dict[str, dict] = {

    # ── Azure Linux / Windows VM ──────────────────────────────────────────────
    "azurerm_linux_virtual_machine": {
        "_requires_identity_block": True,
    },
    "azurerm_windows_virtual_machine": {
        "_requires_identity_block": True,
    },

    # ── Azure PostgreSQL Flexible ─────────────────────────────────────────────
    "azurerm_postgresql_flexible_server": {
        "_requires_ssl_enforcement":      True,
        "_no_ha_on_burstable_sku":        True,
    },

    # ── Azure Linux / Windows Web App ─────────────────────────────────────────
    "azurerm_linux_web_app": {
        "_requires_https_only":     True,
        "_requires_identity_block": True,
    },
    "azurerm_linux_function_app": {
        "_requires_https_only":     True,
        "_requires_identity_block": True,
    },
    "azurerm_windows_function_app": {
        "_requires_https_only":     True,
        "_requires_identity_block": True,
    },

    # ── Azure NSG ─────────────────────────────────────────────────────────────
    "azurerm_network_security_group": {
        "_no_ssh_rdp_from_internet": True,
    },

    # ── AWS S3 Bucket (companion resources required) ──────────────────────────
    "aws_s3_bucket": {
        "_requires_versioning_resource":           True,
        "_requires_encryption_resource":           True,
        "_requires_public_access_block_resource":  True,
    },

    # ── AWS EC2 ───────────────────────────────────────────────────────────────
    "aws_instance": {
        "_requires_ebs_encryption": True,
    },

    # ── AWS Security Group ────────────────────────────────────────────────────
    "aws_security_group": {
        "_no_ssh_rdp_from_internet": True,
    },

    # ── GCP Storage Bucket ────────────────────────────────────────────────────
    "google_storage_bucket": {
        "_requires_versioning_block": True,
    },

    # ── GCP Cloud SQL ─────────────────────────────────────────────────────────
    "google_sql_database_instance": {
        "_requires_backup_enabled": True,
        "_requires_require_ssl":    True,
    },

    # ── GCP Compute Instance ──────────────────────────────────────────────────
    "google_compute_instance": {
        "_requires_shielded_vm": True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Checkov check ID → security category
# ─────────────────────────────────────────────────────────────────────────────
_CHECKOV_ID_MAP: dict[str, str] = {
    # TLS / HTTPS
    "CKV_AZURE_3":    "TLS_ENFORCEMENT",
    "CKV_AZURE_6":    "TLS_ENFORCEMENT",
    "CKV_AZURE_7":    "TLS_ENFORCEMENT",
    "CKV_AZURE_15":   "TLS_ENFORCEMENT",
    "CKV_AZURE_16":   "TLS_ENFORCEMENT",
    "CKV_AZURE_17":   "TLS_ENFORCEMENT",
    "CKV_AZURE_18":   "TLS_ENFORCEMENT",
    "CKV2_AZURE_38":  "TLS_ENFORCEMENT",
    "CKV_AWS_86":     "TLS_ENFORCEMENT",
    "CKV_AWS_184":    "TLS_ENFORCEMENT",
    "CKV_GCP_12":     "TLS_ENFORCEMENT",
    # Encryption at rest
    "CKV_AZURE_4":    "MISSING_ENCRYPTION",
    "CKV_AZURE_35":   "MISSING_ENCRYPTION",
    "CKV_AZURE_36":   "MISSING_ENCRYPTION",
    "CKV_AZURE_43":   "MISSING_ENCRYPTION",
    "CKV_AZURE_44":   "MISSING_ENCRYPTION",
    "CKV_AZURE_52":   "MISSING_ENCRYPTION",
    "CKV2_AZURE_1":   "MISSING_ENCRYPTION",
    "CKV_AWS_19":     "MISSING_ENCRYPTION",
    "CKV_AWS_23":     "MISSING_ENCRYPTION",
    "CKV_AWS_28":     "MISSING_ENCRYPTION",
    "CKV_GCP_22":     "MISSING_ENCRYPTION",
    "CKV_GCP_30":     "MISSING_ENCRYPTION",
    # Public / network exposure
    "CKV_AZURE_59":   "PUBLIC_EXPOSURE",
    "CKV_AZURE_190":  "PUBLIC_EXPOSURE",
    "CKV2_AZURE_40":  "PUBLIC_EXPOSURE",
    "CKV2_AZURE_41":  "PUBLIC_EXPOSURE",
    "CKV_AWS_18":     "PUBLIC_EXPOSURE",
    "CKV_AWS_20":     "PUBLIC_EXPOSURE",
    "CKV_AWS_21":     "PUBLIC_EXPOSURE",
    "CKV_GCP_29":     "PUBLIC_EXPOSURE",
    "CKV_GCP_62":     "PUBLIC_EXPOSURE",
    # IAM / identity
    "CKV_AZURE_80":   "IAM_OVER_PERMISSION",
    "CKV2_AZURE_45":  "IAM_OVER_PERMISSION",
    "CKV_AWS_2":      "IAM_OVER_PERMISSION",
    "CKV_AWS_40":     "IAM_OVER_PERMISSION",
    "CKV_GCP_49":     "IAM_OVER_PERMISSION",
    # Logging / monitoring / diagnostics
    "CKV_AZURE_14":   "MISSING_LOGGING",
    "CKV_AZURE_33":   "MISSING_LOGGING",
    "CKV_AZURE_50":   "MISSING_LOGGING",
    "CKV_AWS_66":     "MISSING_LOGGING",
    "CKV_AWS_70":     "MISSING_LOGGING",
    "CKV_GCP_26":     "MISSING_LOGGING",
}

_CHECKOV_KEYWORD_CATEGORY: dict[str, str] = {
    "tls":          "TLS_ENFORCEMENT",
    "https":        "TLS_ENFORCEMENT",
    "ssl":          "TLS_ENFORCEMENT",
    "traffic only": "TLS_ENFORCEMENT",
    "encrypt":      "MISSING_ENCRYPTION",
    "kms":          "MISSING_ENCRYPTION",
    "cmk":          "MISSING_ENCRYPTION",
    "at rest":      "MISSING_ENCRYPTION",
    "public":       "PUBLIC_EXPOSURE",
    "internet":     "PUBLIC_EXPOSURE",
    "exposed":      "PUBLIC_EXPOSURE",
    "iam":          "IAM_OVER_PERMISSION",
    "role":         "IAM_OVER_PERMISSION",
    "privilege":    "IAM_OVER_PERMISSION",
    "wildcard":     "IAM_OVER_PERMISSION",
    "log":          "MISSING_LOGGING",
    "audit":        "MISSING_LOGGING",
    "monitor":      "MISSING_LOGGING",
    "diagnostic":   "MISSING_LOGGING",
    "retention":    "MISSING_LOGGING",
}

# Human-readable expansion for behavioral constraints
_BEHAVIORAL_HINTS: dict[str, str] = {
    "requires_identity_block":
        'Add identity { type = "SystemAssigned" } block inside the resource.',
    "requires_https_only":
        "Add https_only = true inside the site_config or at the resource level.",
    "requires_ssl_enforcement":
        "Add ssl_enforcement_enabled = true (or equivalent) for PostgreSQL.",
    "no_ha_on_burstable_sku":
        "NEVER add high_availability {} block when sku_name starts with 'B_' (B_Standard_*). "
        "Azure hard-rejects this combination at terraform plan. "
        "high_availability is only valid for General Purpose (GP_*) and Memory Optimized (MO_*) SKUs.",
    "requires_ebs_encryption":
        "Add root_block_device { encrypted = true } inside the resource.",
    "requires_versioning_resource":
        'Generate aws_s3_bucket_versioning with status = "Enabled" as a separate resource.',
    "requires_encryption_resource":
        "Generate aws_s3_bucket_server_side_encryption_configuration as a separate resource.",
    "requires_public_access_block_resource":
        "Generate aws_s3_bucket_public_access_block with all four flags = true.",
    "requires_backup_enabled":
        "Add backup_configuration { enabled = true } inside settings {}.",
    "requires_require_ssl":
        "Add ip_configuration { require_ssl = true } inside settings {}.",
    "requires_versioning_block":
        "Add versioning { enabled = true } block inside the resource.",
    "requires_shielded_vm":
        "Add shielded_instance_config { enable_secure_boot = true } block.",
    "no_ssh_rdp_from_internet":
        "NSG/security-group rules must NOT use 0.0.0.0/0 for SSH (22) or RDP (3389). "
        'Use source_address_prefix = "VirtualNetwork" or a specific CIDR.',
}


class SecurityPolicyEngine:
    """Pre-generation behavioral security constraint layer for Terraform IaC.

    Injects structural hints (missing blocks, companion resources) into the LLM
    prompt before generation.  Attribute-value enforcement is handled post-generation
    by checkov_fixer.py — this class is structural/behavioral only.
    """

    def get_rules_for_resource(self, resource_type: str) -> dict:
        return SECURITY_RULES.get(resource_type, {})

    def get_resources_in_plan(self, migration_plan: dict) -> list[str]:
        resources: list[str] = []
        for r in (migration_plan or {}).get("resources", []):
            tf_resource = (
                r.get("terraform_resource")
                or r.get("target_service")
                or r.get("target_equivalent")
                or ""
            )
            if tf_resource:
                resources.append(tf_resource)
        return resources

    def get_security_prompt_block(self, migration_plan: dict) -> str:
        """Return a formatted security block for injection into the generation prompt.

        Emits only behavioral constraints (_requires_* / _no_*) for resource types
        present in the migration plan.  Attribute-value rules are omitted — checkov_fixer
        handles those deterministically after generation.
        """
        resources = self.get_resources_in_plan(migration_plan)
        lines: list[str] = [
            "## SECURITY POLICY ENGINE — STRUCTURAL REQUIREMENTS",
            "# These constraints describe MISSING STRUCTURE the LLM must generate.",
            "# Attribute-value enforcement (TLS, public access, etc.) is applied",
            "# post-generation by the checkov_fixer — do not duplicate it here.",
            "",
        ]

        matched: list[str] = []
        for resource_type in resources:
            rules = self.get_rules_for_resource(resource_type)
            behavioral = {k: v for k, v in rules.items() if k.startswith("_")}
            if not behavioral:
                continue

            matched.append(resource_type)
            lines.append(f"### {resource_type}")
            for attr in behavioral:
                hint_key = attr.lstrip("_")
                hint = _BEHAVIORAL_HINTS.get(hint_key, "REQUIRED — see structural rules")
                lines.append(f"  # BEHAVIORAL: {hint}")
            lines.append("")

        if not matched:
            lines.append(
                "(No structural requirements for this plan — "
                "apply general baseline below.)"
            )
            lines.append("")

        lines += [
            "### General baseline (ALL resources — no exceptions):",
            "- Encryption at rest: ALWAYS enabled where supported.",
            "- TLS 1.2 minimum: ALWAYS enforced for all data-plane endpoints.",
            "- Public network access: ALWAYS disabled unless explicitly required.",
            "- Managed identity: ALWAYS use SystemAssigned (never access keys/passwords).",
            "- Logging/diagnostics: ALWAYS configure retention_in_days >= 30.",
            "- NSG rules: NEVER allow SSH (22) or RDP (3389) from 0.0.0.0/0.",
            "",
        ]
        return "\n".join(lines)

    def enrich_rag_context(self, rag_context: str, migration_plan: dict) -> str:
        """Inject structural security hints into the RAG context block."""
        security_block = self.get_security_prompt_block(migration_plan)
        sep = "\n" + "=" * 68 + "\n"
        return (
            rag_context
            + sep
            + "=== SECURITY INVARIANTS (SecurityPolicyEngine) ===\n"
            + "NOTE: RAG provides structure. This section provides structural security hints.\n"
            + "Attribute-value enforcement is handled post-generation by checkov_fixer.\n\n"
            + security_block
            + "=" * 68 + "\n"
        )

    def classify_checkov_failures(self, failures: list) -> dict[str, list]:
        """Classify Checkov failure dicts by security category."""
        classified: dict[str, list] = {
            "MISSING_ENCRYPTION": [],
            "TLS_ENFORCEMENT":    [],
            "PUBLIC_EXPOSURE":    [],
            "IAM_OVER_PERMISSION": [],
            "MISSING_LOGGING":    [],
            "OTHER":              [],
        }
        if not isinstance(failures, list):
            return classified

        for item in failures:
            if not isinstance(item, dict):
                continue
            check_id   = item.get("check_id", "")
            check_name = (item.get("check_name") or "").lower()

            category = _CHECKOV_ID_MAP.get(check_id)
            if not category:
                for kw, cat in _CHECKOV_KEYWORD_CATEGORY.items():
                    if kw in check_name:
                        category = cat
                        break
            category = category or "OTHER"
            classified[category].append({
                "check_id":       check_id,
                "check_name":     item.get("check_name", ""),
                "resource":       item.get("resource", ""),
                "file":           item.get("file", ""),
                "evaluated_keys": item.get("evaluated_keys") or [],
                "guideline":      item.get("guideline", ""),
            })
        return classified

    def build_security_violation_context(self, classified: dict[str, list]) -> list[str]:
        """Return human-readable violation strings for prompt feedback."""
        violations: list[str] = []
        for category, items in classified.items():
            for item in items:
                resource   = item.get("resource", "unknown")
                check_id   = item.get("check_id", "")
                check_name = item.get("check_name", "")
                keys       = item.get("evaluated_keys", [])
                key_str    = f" (keys: {', '.join(str(k) for k in keys)})" if keys else ""
                violations.append(
                    f"[{category}] {check_id} — {check_name} on {resource}{key_str}"
                )
        return violations

    def is_security_only_failure(self, tf_valid: bool, failed_checks: list) -> bool:
        """True when Terraform validates OK but Checkov finds security issues."""
        return tf_valid and bool(failed_checks)

    def get_rule_coverage_report(self, migration_plan: dict) -> dict:
        """Return which resources in the plan have known behavioral rules (diagnostic)."""
        resources = self.get_resources_in_plan(migration_plan)
        covered   = [r for r in resources if r in SECURITY_RULES]
        unknown   = [r for r in resources if r not in SECURITY_RULES]
        return {
            "total_resources": len(resources),
            "covered_by_spe":  covered,
            "no_spe_rules":    unknown,
            "coverage_pct":    round(len(covered) / len(resources) * 100, 1) if resources else 0,
        }
