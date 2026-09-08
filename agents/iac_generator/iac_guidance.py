"""
iac_guidance.py — IaC generation guidance for Agent 02.

Renamed from execution_graph.py to avoid confusion with the LangGraph StateGraph.

Single public function: build_iac_guidance()

Returns a prompt context string containing:
  • provider version constraint (prevents LLM version hallucinations)
  • variable catalog (exact names Agent 02 must declare in variables.tf)
  • generation order (infrastructure-layer order for resource files)

Zero LLM calls, zero disk writes, zero Graph RAG calls.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("IaCGuidance")

# Provider version floors — imported from core/constants.py (single source of truth).
from core.constants import PROVIDER_VERSION_FLOORS  # noqa: E402

# Infrastructure-layer order for resource files.
_CATEGORY_ORDER = [
    "network", "iam", "storage", "database",
    "compute", "messaging", "monitoring", "main",
]



# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _infer_category(target_service: str) -> str:
    t = (target_service or "").lower()
    if any(k in t for k in ["iam", "role", "identity", "policy", "service_account"]):
        return "iam"
    if any(k in t for k in ["storage", "bucket", "blob", "s3", "gcs"]):
        return "storage"
    if any(k in t for k in ["database", "cosmos", "dynamodb", "sql", "firestore",
                              "spanner", "bigtable", "postgresql", "mysql"]):
        return "database"
    if any(k in t for k in ["function", "lambda", "cloud_run", "container_app",
                              "virtual_machine", "instance", "compute"]):
        return "compute"
    if any(k in t for k in ["monitor", "log", "cloudwatch", "analytics",
                              "insight", "alert", "metric"]):
        return "monitoring"
    if any(k in t for k in ["network", "vpc", "vnet", "subnet", "firewall",
                              "security_group", "public_ip", "route_table",
                              "internet_gateway"]):
        return "network"
    if any(k in t for k in ["pubsub", "sqs", "sns", "service_bus", "queue", "event"]):
        return "messaging"
    if any(k in t for k in ["kubernetes", "aks", "gke", "eks"]):
        return "compute"
    return "main"


def _derive_resource_group_name(arch: dict) -> str:
    """Derive a resource group name from runtime migration context — no hardcoded fallback.

    Priority: explicit field > sanitized project/app name from architecture specs > empty.
    The LLM is responsible for using an appropriate name when this is empty.
    """
    explicit = arch.get("resource_group_name") or ""
    if explicit:
        return explicit
    # Sanitize the project or app name from architecture metadata if available
    raw = arch.get("project_name") or arch.get("app_name") or arch.get("source_repo_name") or ""
    if raw:
        import re
        sanitized = re.sub(r"[^a-z0-9-]", "-", raw.lower().strip())[:64].strip("-")
        if sanitized:
            return f"rg-{sanitized}"
    return ""


def _derive_gcp_zone(target_region: str) -> str:
    """Derive a GCP zone from the runtime target region — appends '-a' if region is known.

    GCP zone names follow the pattern <region>-<zone-letter>.  Zone 'a' is always
    the first zone in any GCP region, so this is a safe dynamic derivation when no
    explicit zone is specified.  Returns empty string when target_region is not set.
    """
    region = (target_region or "").strip()
    return f"{region}-a" if region else ""


def _base_variables(target_cloud: str, target_region: str, arch: dict) -> dict[str, dict]:
    if target_cloud == "azure":
        return {
            "location": {
                "type": "string",
                "default": target_region or "",
                "description": "Azure region for all resources (required)",
            },
            "resource_group_name": {
                "type": "string",
                "default": _derive_resource_group_name(arch),
                "description": "Name of the Azure resource group (required)",
            },
            "environment": {
                "type": "string",
                "default": "dev",
                "description": "Deployment environment (dev / staging / prod)",
            },
        }
    if target_cloud == "aws":
        return {
            "aws_region": {
                "type": "string",
                "default": target_region or "",
                "description": "AWS region for all resources (required)",
            },
            "environment": {
                "type": "string",
                "default": "dev",
                "description": "Deployment environment",
            },
        }
    if target_cloud == "gcp":
        return {
            "project_id": {
                "type": "string",
                "default": arch.get("project_id") or "",
                "description": "GCP project ID (required)",
            },
            "gcp_region": {
                "type": "string",
                "default": target_region or "",
                "description": "GCP region for all resources (required)",
            },
            "gcp_zone": {
                "type": "string",
                "default": _derive_gcp_zone(target_region),
                "description": "GCP zone for zonal resources — derived from gcp_region if not overridden",
            },
            "environment": {
                "type": "string",
                "default": "dev",
                "description": "Deployment environment",
            },
        }
    return {}


def _variables_for_categories(categories: set[str], target_cloud: str) -> dict[str, dict]:
    extra: dict[str, dict] = {}

    if "compute" in categories:
        extra["admin_username"] = {
            "type": "string", "default": "",
            "description": "Admin username for compute resources (required)",
        }
        extra["admin_password"] = {
            "type": "string", "default": "",
            "sensitive": True,
            "description": "Admin password — must be set before deploy",
        }
        extra["ssh_public_key"] = {
            "type": "string", "default": "",
            "description": "SSH public key for Linux VMs — must be set before deploy",
        }
        if target_cloud == "aws":
            extra["ami_id"] = {
                "type": "string", "default": "",
                "description": "AMI ID for EC2 instances — must be set for your target region",
            }
            extra["instance_type"] = {
                "type": "string", "default": "",
                "description": "EC2 instance type (e.g. t3.micro, m5.large)",
            }
            extra["key_name"] = {
                "type": "string", "default": "",
                "description": "EC2 key pair name — must exist in the target region",
            }

    if "database" in categories:
        extra["db_admin_username"] = {
            "type": "string", "default": "",
            "description": "Database admin username (required)",
        }
        extra["db_admin_password"] = {
            "type": "string", "default": "",
            "sensitive": True,
            "description": "Database admin password — must be set before deploy",
        }

    if "iam" in categories and target_cloud == "azure":
        extra["subscription_id"] = {
            "type": "string", "default": "",
            "description": "Azure subscription ID for IAM scope construction (required)",
        }
        extra["tenant_id"] = {
            "type": "string", "default": "",
            "description": "Azure tenant ID (required)",
        }

    return extra


# ─────────────────────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────────────────────

def build_iac_guidance(
    migration_plan: dict,
    target_cloud: str,
    target_region: str,
    architecture_specs: dict,
) -> str:
    """Returns the IaC guidance block injected into Agent 02's prompt.

    Computes the provider version, variable catalog, and generation order
    from the migration plan. No LLM calls, no disk writes.
    """
    cloud = (target_cloud or "").lower()
    provider_name = {"azure": "azurerm", "gcp": "google", "aws": "aws"}.get(cloud, cloud)
    provider_version = PROVIDER_VERSION_FLOORS.get(provider_name, ">= 1.0")

    categories: set[str] = set()
    resources_by_cat: dict[str, list[str]] = {}
    for r in (migration_plan or {}).get("resources", []):
        strategy = (r.get("strategy") or r.get("strategy_7r") or "").upper()
        if strategy in {"RETAIN", "RETIRE"}:
            continue
        target_svc = (
            r.get("target_service") or r.get("target_equivalent")
            or r.get("terraform_resource") or ""
        )
        cat = r.get("type") or r.get("category") or _infer_category(target_svc)
        categories.add(cat)
        resources_by_cat.setdefault(cat, []).append(target_svc)

    ordered: list[str] = []
    seen: set[str] = set()
    for cat in _CATEGORY_ORDER:
        if cat in categories and cat not in seen:
            seen.add(cat)
            ordered.append(cat)
    for cat in categories:
        if cat not in seen:
            ordered.append(cat)

    variables = _base_variables(cloud, target_region, architecture_specs)
    variables.update(_variables_for_categories(categories, cloud))

    var_lines = "\n".join("  var." + name for name in variables)
    order_lines = "\n".join(
        f"  {i}. {cat} — {resources_by_cat.get(cat, [])}"
        for i, cat in enumerate(ordered, 1)
    )

    logger.info(
        "build_iac_guidance: provider=%s version=%s categories=%s variables=%d",
        provider_name, provider_version, sorted(categories), len(variables),
    )

    _PROVIDER_TEMPLATES: dict[str, str] = {
        "azurerm": (
            'terraform {\n'
            '  required_providers {\n'
            '    azurerm = {\n'
            f'      source  = "hashicorp/azurerm"\n'
            f'      version = "{provider_version}"\n'
            '    }\n'
            '  }\n'
            '}\n'
            '\n'
            'provider "azurerm" {\n'
            '  features {}\n'
            '  subscription_id                 = var.subscription_id\n'
            '  # ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID injected from Vault at runtime.\n'
            '  use_cli                         = false\n'
            '  use_msi                         = false\n'
            '  resource_provider_registrations = "none"\n'
            '}'
        ),
        "aws": (
            'terraform {\n'
            '  required_providers {\n'
            '    aws = {\n'
            f'      source  = "hashicorp/aws"\n'
            f'      version = "{provider_version}"\n'
            '    }\n'
            '  }\n'
            '}\n'
            '\n'
            'provider "aws" {\n'
            f'  region = var.region\n'
            '  # Auth via AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars.\n'
            '}'
        ),
        "google": (
            'terraform {\n'
            '  required_providers {\n'
            '    google = {\n'
            f'      source  = "hashicorp/google"\n'
            f'      version = "{provider_version}"\n'
            '    }\n'
            '  }\n'
            '}\n'
            '\n'
            'provider "google" {\n'
            '  project = var.project_id\n'
            f'  region  = var.region\n'
            '  # Auth via GOOGLE_APPLICATION_CREDENTIALS env var (path to SA JSON).\n'
            '}'
        ),
    }
    provider_tf_template = _PROVIDER_TEMPLATES.get(provider_name, "")
    provider_tf_block = (
        "\n"
        "provider.tf MUST contain EXACTLY this content (do not modify auth flags):\n"
        "```hcl\n"
        + provider_tf_template + "\n"
        "```\n"
    ) if provider_tf_template else ""

    return (
        "## IaC GENERATION GUIDANCE — use these values exactly\n"
        "YOU MUST generate provider.tf and variables.tf using EXACTLY the values below.\n"
        "\n"
        f"Provider:  {provider_name}\n"
        f"Version:   {provider_version}\n"
        f"Region:    {target_region or '(set in variables)'}\n"
        + provider_tf_block
        + "\n"
        "Variables to declare in variables.tf (use these names exactly):\n"
        + var_lines + "\n"
        "\n"
        "Resource generation order:\n"
        + order_lines + "\n"
    )


