"""
python_migrator.py — Agent 02 Python source file migration helpers.

Responsibilities:
  - Rename cloud-specific .env variables and replace credential values with placeholders.
  - Rewrite requirements.txt to swap source-cloud packages for target-cloud equivalents.
  - Migrate Python SDK imports and API calls via GPT-4o (LLM-assisted).

Exported functions:
  _migrate_env_file()             — rename cloud env vars (deterministic)
  _migrate_requirements_txt()     — swap package names (deterministic + LLM fallback)
  _migrate_python_sdk_with_llm()  — rewrite SDK calls via GPT-4o
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from agents.iac_generator.llm_config import _get_llm_02
from agents.iac_generator.iac_system_prompts import PYTHON_MIGRATION_SYSTEM
from agents.iac_generator.hcl_merge import write_terraform_file
from agents.iac_generator.react_tools import (
    validate_terraform_block,
    get_rag_context_for_resource,
    read_generated_files,
)

logger = logging.getLogger("Agent02")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



# ─────────────────────────────────────────────────────────────────────────────
# .env migration — rename cloud-specific env vars, replace values with placeholders
# ─────────────────────────────────────────────────────────────────────────────

# Maps source cloud env var prefixes/names → target cloud equivalent name.
# Values are always replaced with <from-vault> — real values come from SecretBroker
# at deploy time (terraform apply / deploy.sh).
# Key = exact var name or prefix (matched with startswith).
_ENV_VAR_MAP: dict[tuple[str, str], dict[str, str]] = {
    ("aws", "azure"): {
        "AWS_ACCESS_KEY_ID":        "AZURE_CLIENT_ID",
        "AWS_SECRET_ACCESS_KEY":    "AZURE_CLIENT_SECRET",
        "AWS_SESSION_TOKEN":        "AZURE_CLIENT_SECRET",
        "AWS_DEFAULT_REGION":       "AZURE_LOCATION",
        "AWS_REGION":               "AZURE_LOCATION",
        "AWS_ACCOUNT_ID":           "AZURE_SUBSCRIPTION_ID",
        "AWS_S3_BUCKET":            "AZURE_STORAGE_CONTAINER",
        "AWS_S3_":                  "AZURE_STORAGE_",
        "AWS_SQS_":                 "AZURE_SERVICEBUS_",
        "AWS_SNS_":                 "AZURE_SERVICEBUS_",
        "AWS_RDS_":                 "AZURE_SQL_",
        "AWS_DYNAMODB_":            "AZURE_COSMOSDB_",
        "AWS_LAMBDA_":              "AZURE_FUNCTION_",
        "AWS_":                     "AZURE_",
    },
    ("aws", "gcp"): {
        "AWS_ACCESS_KEY_ID":        "GOOGLE_CLIENT_ID",
        "AWS_SECRET_ACCESS_KEY":    "GOOGLE_CLIENT_SECRET",
        "AWS_SESSION_TOKEN":        "GOOGLE_CLIENT_SECRET",
        "AWS_DEFAULT_REGION":       "GOOGLE_CLOUD_REGION",
        "AWS_REGION":               "GOOGLE_CLOUD_REGION",
        "AWS_ACCOUNT_ID":           "GOOGLE_CLOUD_PROJECT",
        "AWS_S3_BUCKET":            "GCS_BUCKET",
        "AWS_S3_":                  "GCS_",
        "AWS_SQS_":                 "PUBSUB_",
        "AWS_SNS_":                 "PUBSUB_",
        "AWS_RDS_":                 "CLOUD_SQL_",
        "AWS_DYNAMODB_":            "FIRESTORE_",
        "AWS_LAMBDA_":              "CLOUD_FUNCTION_",
        "AWS_":                     "GOOGLE_",
    },
    ("azure", "aws"): {
        "AZURE_CLIENT_ID":          "AWS_ACCESS_KEY_ID",
        "AZURE_CLIENT_SECRET":      "AWS_SECRET_ACCESS_KEY",
        "AZURE_TENANT_ID":          "AWS_ACCOUNT_ID",
        "AZURE_SUBSCRIPTION_ID":    "AWS_ACCOUNT_ID",
        "AZURE_LOCATION":           "AWS_REGION",
        "AZURE_STORAGE_CONTAINER":  "AWS_S3_BUCKET",
        "AZURE_STORAGE_":           "AWS_S3_",
        "AZURE_SERVICEBUS_":        "AWS_SQS_",
        "AZURE_SQL_":               "AWS_RDS_",
        "AZURE_COSMOSDB_":          "AWS_DYNAMODB_",
        "AZURE_FUNCTION_":          "AWS_LAMBDA_",
        "AZURE_":                   "AWS_",
    },
    ("azure", "gcp"): {
        "AZURE_CLIENT_ID":          "GOOGLE_CLIENT_ID",
        "AZURE_CLIENT_SECRET":      "GOOGLE_CLIENT_SECRET",
        "AZURE_TENANT_ID":          "GOOGLE_CLOUD_PROJECT",
        "AZURE_SUBSCRIPTION_ID":    "GOOGLE_CLOUD_PROJECT",
        "AZURE_LOCATION":           "GOOGLE_CLOUD_REGION",
        "AZURE_STORAGE_CONTAINER":  "GCS_BUCKET",
        "AZURE_STORAGE_":           "GCS_",
        "AZURE_SERVICEBUS_":        "PUBSUB_",
        "AZURE_SQL_":               "CLOUD_SQL_",
        "AZURE_COSMOSDB_":          "FIRESTORE_",
        "AZURE_FUNCTION_":          "CLOUD_FUNCTION_",
        "AZURE_":                   "GOOGLE_",
    },
    ("gcp", "aws"): {
        "GOOGLE_CLIENT_ID":         "AWS_ACCESS_KEY_ID",
        "GOOGLE_CLIENT_SECRET":     "AWS_SECRET_ACCESS_KEY",
        "GOOGLE_CLOUD_PROJECT":     "AWS_ACCOUNT_ID",
        "GOOGLE_CLOUD_REGION":      "AWS_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS": "AWS_SHARED_CREDENTIALS_FILE",
        "GCS_BUCKET":               "AWS_S3_BUCKET",
        "GCS_":                     "AWS_S3_",
        "PUBSUB_":                  "AWS_SQS_",
        "CLOUD_SQL_":               "AWS_RDS_",
        "FIRESTORE_":               "AWS_DYNAMODB_",
        "CLOUD_FUNCTION_":          "AWS_LAMBDA_",
        "GOOGLE_":                  "AWS_",
    },
    ("gcp", "azure"): {
        "GOOGLE_CLIENT_ID":         "AZURE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET":     "AZURE_CLIENT_SECRET",
        "GOOGLE_CLOUD_PROJECT":     "AZURE_SUBSCRIPTION_ID",
        "GOOGLE_CLOUD_REGION":      "AZURE_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS": "AZURE_CLIENT_CERTIFICATE_PATH",
        "GCS_BUCKET":               "AZURE_STORAGE_CONTAINER",
        "GCS_":                     "AZURE_STORAGE_",
        "PUBSUB_":                  "AZURE_SERVICEBUS_",
        "CLOUD_SQL_":               "AZURE_SQL_",
        "FIRESTORE_":               "AZURE_COSMOSDB_",
        "CLOUD_FUNCTION_":          "AZURE_FUNCTION_",
        "GOOGLE_":                  "AZURE_",
    },
}


def _migrate_env_file(
    content: str,
    source_cloud: str,
    target_cloud: str,
) -> str:
    """Rename cloud-specific env vars and replace their values with <from-vault>.

    Non-cloud vars (DATABASE_URL, PORT, DEBUG, etc.) are preserved as-is.
    Credential values are never carried over — the runner injects real values
    from SecretBroker at deploy time.
    """
    mapping = _ENV_VAR_MAP.get((source_cloud, target_cloud), {})
    output_lines: list[str] = [
        f"# .env migrated from {source_cloud.upper()} → {target_cloud.upper()} by Cloud Migrator",
        "# Cloud-specific vars have been renamed. Values marked <from-vault> are",
        "# injected at deploy time from SecretBroker (Vault / encrypted-file backend).",
        "",
    ]

    for raw_line in content.splitlines():
        stripped = raw_line.strip()

        if not stripped or stripped.startswith("#"):
            output_lines.append(raw_line)
            continue

        if "=" not in stripped:
            output_lines.append(raw_line)
            continue

        var_name, _, _value = stripped.partition("=")
        var_name = var_name.strip()

        if not mapping:
            output_lines.append(raw_line)
            continue

        # Find longest-matching prefix (exact match first, then prefix)
        matched_target = None
        best_len = 0
        for src_key, tgt_key in mapping.items():
            if var_name == src_key and len(src_key) > best_len:
                matched_target = tgt_key
                best_len = len(src_key)
            elif var_name.startswith(src_key) and len(src_key) > best_len:
                suffix = var_name[len(src_key):]
                matched_target = tgt_key + suffix
                best_len = len(src_key)

        if matched_target:
            output_lines.append(f"# renamed from: {var_name}")
            output_lines.append(f"{matched_target}=<from-vault>")
        else:
            output_lines.append(raw_line)

    result = "\n".join(output_lines)
    logger.info(
        f"_migrate_env_file: {source_cloud}→{target_cloud}, "
        f"{sum(1 for l in output_lines if l.startswith('# renamed'))} var(s) renamed"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# requirements.txt migration — deterministic package substitution
# ─────────────────────────────────────────────────────────────────────────────

# Keyed by (source_cloud, target_cloud).
# Each entry maps a package prefix → list of replacement packages.
# Empty list means the package is removed (internal dep, no equivalent).
# Order matters: first match wins.
_REQUIREMENTS_PACKAGE_MAP: dict[tuple[str, str], dict[str, list[str]]] = {
    ("aws", "azure"): {
        "boto3":              ["azure-storage-blob", "azure-identity"],
        "botocore":           [],
        "aiobotocore":        ["azure-storage-blob"],
        "s3transfer":         [],
        "sagemaker":          ["azure-ai-ml"],
        "aws-cdk-lib":        [],
        "aws-cdk":            [],
        "langchain-aws":      ["langchain-azure-dynamic-sessions"],
        "langchain_aws":      ["langchain-azure-dynamic-sessions"],
        "moto":               [],
    },
    ("aws", "gcp"): {
        "boto3":              ["google-cloud-storage", "google-cloud-compute"],
        "botocore":           [],
        "aiobotocore":        ["google-cloud-storage"],
        "s3transfer":         [],
        "sagemaker":          ["google-cloud-aiplatform"],
        "aws-cdk-lib":        [],
        "aws-cdk":            [],
        "langchain-aws":      ["langchain-google-vertexai"],
        "langchain_aws":      ["langchain-google-vertexai"],
        "moto":               [],
    },
    ("azure", "aws"): {
        "azure-storage-blob": ["boto3"],
        "azure-storage-queue":["boto3"],
        "azure-storage-file": ["boto3"],
        "azure-identity":     [],
        "azure-mgmt":         ["boto3"],
        "azure-ai-ml":        ["sagemaker"],
        "azure-ai-":          ["boto3"],
        "azure-":             ["boto3"],
        "langchain-azure":    ["langchain-aws"],
        "langchain_azure":    ["langchain-aws"],
        "msrest":             [],
    },
    ("azure", "gcp"): {
        "azure-storage-blob": ["google-cloud-storage"],
        "azure-storage-queue":["google-cloud-pubsub"],
        "azure-identity":     ["google-auth"],
        "azure-mgmt":         ["google-cloud-resource-manager"],
        "azure-ai-ml":        ["google-cloud-aiplatform"],
        "azure-":             ["google-cloud-storage"],
        "langchain-azure":    ["langchain-google-vertexai"],
        "langchain_azure":    ["langchain-google-vertexai"],
        "msrest":             [],
    },
    ("gcp", "aws"): {
        "google-cloud-storage":      ["boto3"],
        "google-cloud-compute":      ["boto3"],
        "google-cloud-pubsub":       ["boto3"],
        "google-cloud-aiplatform":   ["sagemaker"],
        "google-cloud-bigquery":     ["boto3"],
        "google-cloud-":             ["boto3"],
        "google-auth":               [],
        "vertexai":                  ["sagemaker"],
        "langchain-google-vertexai": ["langchain-aws"],
        "langchain_google":          ["langchain-aws"],
    },
    ("gcp", "azure"): {
        "google-cloud-storage":      ["azure-storage-blob"],
        "google-cloud-compute":      ["azure-mgmt-compute"],
        "google-cloud-pubsub":       ["azure-servicebus"],
        "google-cloud-aiplatform":   ["azure-ai-ml"],
        "google-cloud-bigquery":     ["azure-synapse-spark"],
        "google-cloud-":             ["azure-storage-blob"],
        "google-auth":               ["azure-identity"],
        "vertexai":                  ["azure-ai-ml"],
        "langchain-google-vertexai": ["langchain-azure-dynamic-sessions"],
        "langchain_google":          ["langchain-azure-dynamic-sessions"],
    },
}


def _migrate_requirements_txt(
    content: str,
    source_cloud: str,
    target_cloud: str,
) -> str:
    """Replace cloud SDK packages in requirements.txt with target-cloud equivalents.

    Non-SDK lines (comments, blank lines, non-cloud packages) are preserved as-is.
    Removed packages (no equivalent) get a comment explaining why.
    Already-added replacements are deduplicated.
    """
    mapping = _REQUIREMENTS_PACKAGE_MAP.get((source_cloud, target_cloud), {})
    if not mapping:
        return content

    added: set[str] = set()
    output_lines: list[str] = []

    for raw_line in content.splitlines():
        stripped = raw_line.strip()

        # Pass through comments and blank lines unchanged
        if not stripped or stripped.startswith("#"):
            output_lines.append(raw_line)
            continue

        # Extract package name (ignore version specifiers and extras)
        pkg_name = stripped.split("=")[0].split(">")[0].split("<")[0].split("[")[0].strip().lower()

        matched_prefix = None
        for prefix in mapping:
            if pkg_name == prefix or pkg_name.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            # Non-cloud package — keep unchanged
            output_lines.append(raw_line)
            continue

        replacements = mapping[matched_prefix]
        if not replacements:
            output_lines.append(f"# removed: {stripped}  (no equivalent on {target_cloud})")
            continue

        output_lines.append(f"# replaced: {stripped}")
        for rep in replacements:
            if rep not in added:
                output_lines.append(rep)
                added.add(rep)

    result = "\n".join(output_lines)
    logger.info(
        f"_migrate_requirements_txt: {source_cloud}→{target_cloud}, "
        f"{len(added)} replacement(s) added"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Python SDK migration via LLM (no fallback — fails hard on LLM error)
# ─────────────────────────────────────────────────────────────────────────────

def _build_comprehensive_migration_context(
    migration_plan: dict,
    source_cloud: str,
    target_cloud: str,
    ai_stack: dict,
) -> str:
    """Build a rich migration context string from the full migration plan.

    This replaces the old single-sdk_changes approach: instead of telling the LLM
    "replace import X with import Y", we tell it the full picture — every service
    being migrated, every SDK change, every env var rename — so it can handle ALL
    Python files in the repo, not just AI/LLM ones.
    """
    lines: list[str] = [
        f"## Migration: {source_cloud.upper()} → {target_cloud.upper()}",
        "",
        "### Services being migrated (from migration plan):",
    ]

    # Per-resource SDK and service info
    _SDK_HINTS: dict[tuple[str, str], str] = {
        # (source_tf_type, target_cloud) → SDK migration hint
        ("aws_s3_bucket",      "azure"): "boto3.client('s3') / boto3.resource('s3') → azure-storage-blob BlobServiceClient. Replace os.getenv('S3_BUCKET','AWS_S3_BUCKET') with os.getenv('AZURE_STORAGE_CONTAINER').",
        ("aws_s3_bucket",      "gcp"):   "boto3.client('s3') → google-cloud-storage storage.Client(). Replace S3_BUCKET env var with GCS_BUCKET.",
        ("aws_s3_bucket",      "aws"):   "",
        ("aws_db_instance",    "azure"): "Replace RDS connection strings (postgres://...rds.amazonaws.com/...) with Azure PostgreSQL URL. Replace RDS_URL / DATABASE_URL env var with AZURE_POSTGRESQL_URL.",
        ("aws_db_instance",    "gcp"):   "Replace RDS connection strings with Cloud SQL connection string. Update DATABASE_URL env var.",
        ("aws_sqs_queue",      "azure"): "boto3.client('sqs') → azure-servicebus ServiceBusClient.",
        ("aws_sqs_queue",      "gcp"):   "boto3.client('sqs') → google-cloud-pubsub PublisherClient/SubscriberClient.",
        ("aws_lambda_function","azure"): "boto3.client('lambda') → azure-functions or azure-mgmt-web.",
        ("aws_lambda_function","gcp"):   "boto3.client('lambda') → google-cloud-functions.",
        ("aws_dynamodb_table", "azure"): "boto3.resource('dynamodb') → azure-cosmos CosmosClient.",
        ("aws_dynamodb_table", "gcp"):   "boto3.resource('dynamodb') → google-cloud-firestore firestore.Client().",
    }

    for r in migration_plan.get("resources", []):
        src_svc = r.get("source_service", "")
        tgt_svc = r.get("target_service", "")
        strategy = r.get("strategy_7r") or r.get("strategy", "")
        sdk_note = r.get("sdk_note") or r.get("reasoning", "")
        category = r.get("category", "")
        hint = _SDK_HINTS.get((src_svc, target_cloud.lower()), "")

        lines.append(f"- {src_svc} → {tgt_svc} [{strategy}]")
        if hint:
            lines.append(f"  SDK: {hint}")
        if sdk_note and "sdk" in sdk_note.lower():
            lines.append(f"  Note: {sdk_note[:200]}")

    # AI stack info
    llm_provider = ai_stack.get("llm_provider", "")
    vector_store = ai_stack.get("vector_store", "")
    providers = ai_stack.get("providers_detected", [])

    if llm_provider or providers:
        lines.append("")
        lines.append("### AI Stack detected in Python code:")
        if llm_provider:
            lines.append(f"- LLM provider: {llm_provider}")
        if vector_store:
            lines.append(f"- Vector store: {vector_store}")
        if providers:
            lines.append(f"- Classes/imports detected: {', '.join(providers[:10])}")

    # Generic SDK migration rules for this cloud pair
    lines.append("")
    lines.append("### Generic SDK migration rules:")
    if source_cloud.lower() == "aws" and target_cloud.lower() == "azure":
        lines += [
            "- boto3.client('s3') / boto3.resource('s3') → azure.storage.blob.BlobServiceClient",
            "- boto3.client('rds') or psycopg2 with RDS host → Azure PostgreSQL flexible server URL",
            "- boto3.client('sqs') → azure.servicebus.ServiceBusClient",
            "- boto3.client('secretsmanager') → azure.keyvault.secrets.SecretClient",
            "- boto3.client('lambda') → no direct SDK equivalent, add TODO",
            "- OpenAI() → AzureOpenAI(azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'), api_key=os.getenv('AZURE_OPENAI_API_KEY'), api_version='2024-02-01')",
            "- os.getenv('OPENAI_API_KEY') → os.getenv('AZURE_OPENAI_API_KEY')",
            "- RDS_URL / DATABASE_URL containing 'rds.amazonaws.com' → AZURE_POSTGRESQL_URL",
            "- S3_BUCKET / AWS_S3_BUCKET → AZURE_STORAGE_CONTAINER",
            "- AWS_REGION / AWS_DEFAULT_REGION → AZURE_LOCATION",
        ]
    elif source_cloud.lower() == "aws" and target_cloud.lower() == "gcp":
        lines += [
            "- boto3.client('s3') → google.cloud.storage.Client()",
            "- boto3.client('sqs') → google.cloud.pubsub_v1.PublisherClient()",
            "- OpenAI() → google.cloud.aiplatform or vertexai SDK",
            "- S3_BUCKET → GCS_BUCKET",
            "- AWS_REGION → GOOGLE_CLOUD_REGION",
        ]
    elif source_cloud.lower() == "azure" and target_cloud.lower() == "aws":
        lines += [
            "- azure.storage.blob.BlobServiceClient → boto3.client('s3')",
            "- azure.servicebus → boto3.client('sqs')",
            "- AzureOpenAI() → OpenAI() or boto3.client('bedrock-runtime')",
            "- AZURE_STORAGE_CONTAINER → AWS_S3_BUCKET",
            "- AZURE_LOCATION → AWS_REGION",
        ]

    lines.append("")
    lines.append("### Environment variables to rename (in os.getenv calls and .env files):")
    # Pull from the static map in _ENV_VAR_MAP for the right cloud pair
    _pair = (source_cloud.lower(), target_cloud.lower())
    env_map = _ENV_VAR_MAP.get(_pair, {})
    for src_var, tgt_var in list(env_map.items())[:12]:  # top 12 to keep prompt short
        lines.append(f"- {src_var} → {tgt_var}")

    return "\n".join(lines)


def _migrate_python_sdk_with_llm(
    filename: str,
    content: str,
    sdk_changes: dict,
    source_cloud: str,
    target_cloud: str,
    migration_context: str = "",
) -> str:
    """Migrate one Python file's SDK calls. Raises on LLM failure (no fallback)."""
    from langchain_core.messages import SystemMessage

    # Use rich migration_context if available; fall back to single-sdk summary
    if migration_context:
        context_block = migration_context
    else:
        src_import = sdk_changes.get("source_import", "")
        tgt_import = sdk_changes.get("target_import", "")
        context_block = (
            f"Source SDK import: {src_import}\n"
            f"Target SDK import: {tgt_import}\n"
            f"Source cloud: {source_cloud}\n"
            f"Target cloud: {target_cloud}"
        )

    prompt = (
        f"Migrate this Python file ({filename}) from {source_cloud.upper()} to {target_cloud.upper()}.\n\n"
        f"Migration context:\n{context_block}\n\n"
        f"Python file to migrate:\n```python\n{content}\n```\n\n"
        f"Apply ONLY the changes relevant to THIS file's actual imports and calls. "
        f"Output only the migrated Python file content — no explanations, no markdown fences."
    )

    llm = _get_llm_02()
    result = llm.invoke([
        SystemMessage(content=PYTHON_MIGRATION_SYSTEM),
        HumanMessage(content=prompt),
    ])
    migrated = result.content if hasattr(result, "content") else str(result)
    if isinstance(migrated, list):
        migrated = "\n".join(str(p) for p in migrated)
    if migrated.startswith("```"):
        lines = migrated.splitlines()
        migrated = "\n".join(ln for ln in lines if not ln.startswith("```"))
    logger.info(f"_migrate_python_sdk_with_llm: migrated {filename} ({len(migrated)} chars)")
    return migrated


# ─────────────────────────────────────────────────────────────────────────────
# Agent tools list
# ─────────────────────────────────────────────────────────────────────────────

_agent_02_tools = [
    write_terraform_file,
    validate_terraform_block,
    get_rag_context_for_resource,
    read_generated_files,
]

# Maximum RAG context size injected into the prompt.
# Azure GPT-4o has a 128K context window; we reserve room for the system
# prompt, resources table, and LLM reasoning. Very large RAG blocks
# (>80K chars) silently caused invoke() to return {} in <50ms on some
# Azure deployments — the request was rejected client-side before hitting
# the network. Truncating to 60K chars resolves this.
_MAX_RAG_CONTEXT_CHARS = 90_000

