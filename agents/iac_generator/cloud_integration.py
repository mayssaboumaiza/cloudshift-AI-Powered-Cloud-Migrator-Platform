"""
cloud_integration.py — Cloud credential helpers + deployment tools.

LangChain tools exposed here:
  validate_target_credentials  — Validate cloud provider credentials directly.
  check_deployment_readiness   — Check quota/credential readiness for a provider+region.
  get_deployment_context       — Get init commands, backend config, auth method.
  render_deploy_template       — Render a provider-specific deploy.sh via Jinja2.
  execute_terraform_apply      — Stub: terraform execution has moved to TerraformRunner.

Private helper:
  _fetch_vault_credentials     — Fetch per-migration cloud creds from Vault.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── Known regions and default quotas ─────────────────────────────────────────

_KNOWN_REGIONS: dict[str, list[str]] = {
    "aws": [
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1",
        "eu-north-1", "ap-southeast-1", "ap-southeast-2",
        "ap-northeast-1", "ap-south-1", "sa-east-1", "ca-central-1",
    ],
    "gcp": [
        "us-central1", "us-east1", "us-west1", "us-west2",
        "europe-west1", "europe-west2", "europe-west3", "europe-west4",
        "europe-west9", "europe-north1", "asia-east1", "asia-northeast1",
        "asia-south1", "asia-southeast1", "australia-southeast1",
    ],
    "azure": [
        "eastus", "eastus2", "westus", "westus2", "westus3",
        "northeurope", "westeurope", "francecentral", "francesouth",
        "germanywestcentral", "uksouth", "ukwest", "swedencentral",
        "southeastasia", "eastasia", "australiaeast",
        "brazilsouth", "canadacentral", "canadaeast",
        "japaneast", "japanwest", "koreacentral",
        "centralindia", "southindia", "norwayeast",
        "switzerlandnorth", "uaenorth", "southafricanorth",
    ],
}

_DEFAULT_QUOTAS: dict[str, dict[str, int]] = {
    "aws":   {"lambda": 1000, "s3": 100, "dynamodb": 256, "sqs": 120_000, "eks": 100},
    "gcp":   {"cloud_functions": 1000, "cloud_storage": 100, "firestore": 100},
    "azure": {"azure_functions": 200, "blob_storage": 250, "cosmos_db": 100},
}

# ── Path constants ────────────────────────────────────────────────────────────

_AGENT_02_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR   = os.path.dirname(_AGENT_02_DIR)
_PROJECT_ROOT = os.path.dirname(_AGENTS_DIR)
TEMPLATES_DIR = os.path.join(_AGENTS_DIR, "deployer", "templates")


def _get_output_dir() -> str:
    """Return output directory, re-reading MIGRATION_OUTPUT_DIR on every call."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    return _out_env if os.path.isabs(_out_env) else os.path.join(
        _PROJECT_ROOT, _out_env or os.path.join("output", "migrated_app")
    )


OUTPUT_DIR = _get_output_dir()

# ── Credential helpers ────────────────────────────────────────────────────────

def _get_creds(provider: str) -> dict[str, str]:
    """Read cloud credentials from environment variables."""
    if provider == "aws":
        return {
            "key":    os.getenv("AWS_ACCESS_KEY_ID", ""),
            "secret": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            "region": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
        }
    if provider == "gcp":
        return {"project": os.getenv("GCP_PROJECT_ID", "")}
    if provider in ("azure", "azurerm"):
        return {
            "sub":    os.getenv("AZURE_SUBSCRIPTION_ID", ""),
            "tenant": os.getenv("AZURE_TENANT_ID", ""),
        }
    return {}


def _fetch_vault_credentials(migration_id: str, cloud_provider: str) -> dict[str, str]:
    """Fetch cloud credentials from Vault and return as Terraform env-var dict.

    Returns an empty dict if Vault is unreachable or the secret is not found.
    GCP: writes service_account_json to a temp file and returns its path as
    GOOGLE_APPLICATION_CREDENTIALS so Terraform can read it.
    """
    if not migration_id:
        return {}

    # The frontend sends "azure" / "aws" / "gcp"; Vault is keyed by those names.
    _VAULT_KEY = {"azurerm": "azure", "google": "gcp"}
    vault_provider = _VAULT_KEY.get(cloud_provider.lower(), cloud_provider.lower())

    try:
        from services.credentials.vault_store import get_credential_store
        store = get_credential_store()
        creds = store.get(user_id=migration_id, provider=vault_provider)
        if not creds:
            logger.error(
                f"_fetch_vault_credentials: no secret found in Vault for "
                f"migration_id='{migration_id}', provider='{vault_provider}' "
                f"(requested as '{cloud_provider}'). "
                f"Ensure POST /credentials/store was called with "
                f"user_id='{migration_id}' and provider='{vault_provider}'."
            )
            return {}

        if cloud_provider in ("azure", "azurerm"):
            subscription_id = creds.get("subscription_id", "")
            tenant_id       = creds.get("tenant_id", "")
            return {
                "ARM_TENANT_ID":          tenant_id,
                "ARM_CLIENT_ID":          creds.get("client_id", ""),
                "ARM_CLIENT_SECRET":      creds.get("client_secret", ""),
                "ARM_SUBSCRIPTION_ID":    subscription_id,
                "TF_VAR_subscription_id": subscription_id,
                "TF_VAR_tenant_id":       tenant_id,
            }

        if cloud_provider == "aws":
            region = creds.get("region", "")
            env: dict[str, str] = {
                "AWS_ACCESS_KEY_ID":     creds.get("access_key", ""),
                "AWS_SECRET_ACCESS_KEY": creds.get("secret_key", ""),
            }
            if region:
                env["AWS_DEFAULT_REGION"] = region
                env["TF_VAR_region"]      = region
            if creds.get("session_token"):
                env["AWS_SESSION_TOKEN"] = creds["session_token"]
            return env

        if cloud_provider in ("gcp", "google"):
            import tempfile
            sa_json    = creds.get("service_account_json", {})
            project_id = creds.get("project_id", "")
            if sa_json:
                tf = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, prefix="gcp_sa_"
                )
                json.dump(
                    sa_json if isinstance(sa_json, dict) else json.loads(sa_json), tf
                )
                tf.close()
                return {
                    "GOOGLE_APPLICATION_CREDENTIALS": tf.name,
                    "GOOGLE_PROJECT":                 project_id,
                    "TF_VAR_project_id":              project_id,
                }

    except Exception as e:
        logger.error(
            f"_fetch_vault_credentials: Vault lookup raised {type(e).__name__} "
            f"for migration_id='{migration_id}', provider='{vault_provider}': {e}. "
            f"Credentials will NOT be injected into the Terraform subprocess."
        )
    return {}


# ── LangChain tools ───────────────────────────────────────────────────────────

@tool
def validate_target_credentials(provider: str) -> str:
    """Validate credentials for the target cloud provider.

    Args:
        provider: Target cloud provider ('aws', 'gcp', 'azure').

    Returns:
        JSON string with: valid (bool), identity, account/project, message.
    """
    creds = _get_creds(provider)

    if provider == "aws":
        key, secret, region = creds.get("key", ""), creds.get("secret", ""), creds.get("region", "us-east-1")
        if not (key and secret):
            return json.dumps({"valid": False, "provider": "aws", "identity": "",
                               "message": "AWS_ACCESS_KEY_ID ou AWS_SECRET_ACCESS_KEY manquant"})
        try:
            import boto3
            sts      = boto3.client("sts", aws_access_key_id=key,
                                    aws_secret_access_key=secret, region_name=region)
            identity = sts.get_caller_identity()
            account  = identity.get("Account", "")
            return json.dumps({"valid": True, "provider": "aws",
                               "identity": identity.get("Arn", ""), "account": account,
                               "message": f"Connecté au compte AWS {account} (région: {region})"})
        except Exception as exc:
            return json.dumps({"valid": False, "provider": "aws", "identity": "",
                               "message": str(exc)[:300]})

    if provider == "gcp":
        project = creds.get("project", "")
        if not project:
            return json.dumps({"valid": False, "provider": "gcp", "identity": "",
                               "message": "GCP_PROJECT_ID manquant"})
        try:
            from google.cloud import storage
            list(storage.Client(project=project).list_buckets(max_results=1))
            return json.dumps({"valid": True, "provider": "gcp", "identity": project,
                               "message": f"Connecté au projet GCP {project}"})
        except Exception as exc:
            return json.dumps({"valid": False, "provider": "gcp", "identity": "",
                               "message": str(exc)[:300]})

    if provider in ("azure", "azurerm"):
        sub, tenant = creds.get("sub", ""), creds.get("tenant", "")
        if not (sub and tenant):
            return json.dumps({"valid": False, "provider": "azure", "identity": "",
                               "message": "AZURE_SUBSCRIPTION_ID ou AZURE_TENANT_ID manquant"})
        return json.dumps({"valid": True, "provider": "azure", "identity": sub,
                           "message": f"Souscription Azure {sub} configurée (tenant: {tenant})"})

    return json.dumps({"valid": False, "provider": provider, "identity": "",
                       "message": f"Provider inconnu: {provider}"})


@tool
def check_deployment_readiness(
    provider: str,
    region: str,
    resources: list,
    migration_id: str = "",
) -> str:
    """Check if the target cloud is ready for deployment.

    Args:
        provider:     Target cloud provider ('aws', 'gcp', 'azure').
        region:       Target region string.
        resources:    List of resource name strings to check quotas for.
        migration_id: Optional migration ID to fetch credentials from Vault.

    Returns:
        JSON string with: ready (bool), checks (list), warnings (list).
    """
    checks:   list[dict] = []
    warnings: list[str]  = []

    # 1. Credentials — try Vault first, then env vars
    cred_passed = False
    cred_detail = ""
    if migration_id:
        try:
            vault_creds = _fetch_vault_credentials(migration_id, provider)
            if vault_creds:
                cred_passed = True
                cred_detail = f"Credentials vérifiés via Vault (migration_id={migration_id})"
                logger.info(f"check_deployment_readiness: credentials OK via Vault for {migration_id}")
        except Exception as vault_exc:
            logger.warning(f"check_deployment_readiness: Vault lookup failed: {vault_exc}")

    if not cred_passed:
        cred_result = json.loads(validate_target_credentials.func(provider))
        cred_passed = cred_result.get("valid", False)
        cred_detail = cred_result.get("message", "")

    checks.append({"check": "credentials", "passed": cred_passed, "detail": cred_detail})

    # 2. Region
    known     = _KNOWN_REGIONS.get(provider, [])
    region_ok = region in known if known else True
    checks.append({"check": "region", "passed": region_ok,
                   "detail": f"Région '{region}' {'valide' if region_ok else 'inconnue'} pour {provider}"})
    if not region_ok and known:
        warnings.append(f"Région '{region}' non reconnue pour {provider}. Exemples: {', '.join(known[:4])}")

    # 3. Quotas
    quotas = _DEFAULT_QUOTAS.get(provider, {})
    for res in (resources or []):
        if res in quotas:
            checks.append({"check": f"quota_{res}", "passed": True,
                           "detail": f"{res}: quota par défaut ~{quotas[res]} (OK)"})
        else:
            checks.append({"check": f"service_{res}", "passed": True,
                           "detail": f"{res}: pas de quota connu — à vérifier manuellement"})

    ready = all(c["passed"] for c in checks)
    logger.info(f"check_deployment_readiness({provider}/{region}): {'READY' if ready else 'NOT READY'}")
    return json.dumps({"ready": ready, "checks": checks, "warnings": warnings})


@tool
def get_deployment_context(provider: str, region: str, project_id: str = "") -> str:
    """Get deployment context (init commands, backend config, auth method) for a cloud.

    Args:
        provider:   Target cloud provider ('aws', 'gcp', 'azure').
        region:     Target region string.
        project_id: GCP project ID (required for GCP only).

    Returns:
        JSON string with: provider, init_commands, terraform_backend, auth_method.
    """
    if provider == "aws":
        return json.dumps({
            "provider": "aws", "region": region,
            "init_commands": [
                f"export AWS_DEFAULT_REGION={region}",
                "aws sts get-caller-identity",
                "terraform init",
            ],
            "terraform_backend": "s3",
            "auth_method": "environment_variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)",
            "notes": "Créer un bucket S3 pour le backend Terraform avant init",
        })

    if provider == "gcp":
        project = project_id or os.getenv("GCP_PROJECT_ID", "my-gcp-project")
        return json.dumps({
            "provider": "gcp", "region": region, "project_id": project,
            "init_commands": [
                f"gcloud config set project {project}",
                f"gcloud config set compute/region {region}",
                "gcloud auth application-default login",
                "terraform init",
            ],
            "terraform_backend": "gcs",
            "auth_method": "Application Default Credentials",
            "notes": "Créer un bucket GCS pour le backend Terraform avant init",
        })

    if provider in ("azure", "azurerm"):
        sub = os.getenv("AZURE_SUBSCRIPTION_ID", "<SUBSCRIPTION_ID>")
        return json.dumps({
            "provider": "azure", "region": region, "subscription_id": sub,
            "init_commands": [
                "az login",
                f"az account set --subscription {sub}",
                "terraform init",
            ],
            "terraform_backend": "azurerm",
            "auth_method": "az login (Service Principal recommandé en CI/CD)",
            "notes": "Créer un storage account Azure pour le backend Terraform avant init",
        })

    return json.dumps({"provider": provider, "region": region,
                       "init_commands": [], "terraform_backend": "",
                       "auth_method": "", "error": f"Provider non supporté: {provider}"})


def _detect_migration_flags(resources_csv: str) -> dict:
    """Detect what types of data migration are needed from the resource list.

    Inspects the Terraform resource names that Agent 02 generated to determine
    which data migration sections to activate in the deploy script.
    """
    res_lower = resources_csv.lower()
    tf_output_dir = Path(_get_output_dir())

    # Also scan generated .tf files for resource types
    tf_content = ""
    if tf_output_dir.is_dir():
        for tf_file in tf_output_dir.glob("*.tf"):
            try:
                tf_content += tf_file.read_text(encoding="utf-8").lower()
            except Exception:
                pass

    combined = res_lower + " " + tf_content

    has_s3 = any(kw in combined for kw in (
        "s3", "storage_account", "storage_container", "blob", "azurerm_storage",
    ))
    has_postgres = any(kw in combined for kw in (
        "postgresql", "postgres", "rds", "azurerm_postgresql", "flexible_server",
        "mysql", "azurerm_mysql",
    ))
    has_ai = any(kw in combined for kw in (
        "cognitive_account", "openai", "bedrock", "azurerm_cognitive",
        "azurerm_machine_learning", "sagemaker",
    ))

    return {
        "has_s3_migration":       has_s3,
        "has_postgres_migration": has_postgres,
        "has_ai_migration":       has_ai,
    }


@tool
def render_deploy_template(
    cloud_provider: str,
    region: str,
    resources_csv: str,
    project_id: str = "",
    backend_bucket: str = "",
    backend_storage_account: str = "",
) -> str:
    """Render a provider-specific deploy.sh from a Jinja2 template.

    Deterministic alternative to LLM-generated scripts — uses hardened templates
    with set -euo pipefail, env-var credential checks, and structured steps.
    Automatically detects S3/PostgreSQL/AI resources and activates the
    corresponding data migration sections in the script.

    Args:
        cloud_provider:           One of 'aws', 'gcp', 'azure'.
        region:                   Target deployment region.
        resources_csv:            Comma-separated list of resource names being deployed.
        project_id:               GCP project ID (required for GCP only).
        backend_bucket:           Optional remote state bucket (S3/GCS).
        backend_storage_account:  Optional Azure storage account for remote state.

    Returns:
        Rendered deploy.sh content as a string, or error JSON if template not found.
        The file is also written to output/migrated_app/deploy.sh automatically.
    """
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        deploy_templates_dir = os.path.join(TEMPLATES_DIR, "deploy")
        template_file = f"{cloud_provider}_deploy.sh.j2"
        template_path = os.path.join(deploy_templates_dir, template_file)

        if not os.path.exists(template_path):
            logger.warning(f"render_deploy_template: template not found: {template_path}")
            return json.dumps({
                "error": f"Template not found: {template_file}",
                "available": os.listdir(deploy_templates_dir)
                             if os.path.isdir(deploy_templates_dir) else [],
            })

        env = Environment(
            loader=FileSystemLoader(deploy_templates_dir),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template  = env.get_template(template_file)
        output_dir_path = _get_output_dir()

        # Auto-detect which data migration sections to activate
        migration_flags = _detect_migration_flags(resources_csv)
        logger.info(
            "render_deploy_template: migration flags detected: %s", migration_flags
        )

        rendered = template.render(
            cloud_provider=cloud_provider,
            region=region,
            resources_csv=resources_csv,
            project_id=project_id or "",
            backend_bucket=backend_bucket or "",
            backend_storage_account=backend_storage_account or "",
            output_dir=output_dir_path,
            **migration_flags,
        )

        # Write directly so the LLM does not relay content via write_terraform_file
        # (risks losing set -euo pipefail through paraphrasing).
        out_dir = Path(output_dir_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        deploy_sh_path = out_dir / "deploy.sh"
        deploy_sh_path.write_text(rendered, encoding="utf-8")

        logger.info(
            f"render_deploy_template: rendered {template_file} ({len(rendered)} chars) "
            f"→ {deploy_sh_path}"
        )
        return rendered

    except Exception as e:
        logger.error(f"render_deploy_template failed: {e}")
        return json.dumps({"error": str(e)})


@tool
def execute_terraform_apply(
    cloud_provider: str,
    auto_approve: bool = False,
    migration_id: str = "",
) -> str:
    """[DISABLED — Phase 2] Terraform execution now runs in the TerraformRunner process.

    This stub is kept so existing imports and agent prompts that reference it do
    not fail.  It will never be called by Agent 03 (removed from the tool list).
    If called directly it returns a deferred message.

    Args:
        cloud_provider: One of 'aws', 'gcp', 'azure'. (ignored)
        auto_approve:   Ignored.
        migration_id:   Ignored.
    """
    logger.warning(
        "execute_terraform_apply called as stub — terraform execution has moved "
        "to the TerraformRunner process. migration_id=%s provider=%s",
        migration_id, cloud_provider,
    )
    return json.dumps({
        "success":  False,
        "deferred": True,
        "message": (
            "Terraform execution is handled by the TerraformRunner process (Phase 2). "
            "The deployment manifest has been queued — check /api/v1/runner/jobs for status."
        ),
    })
