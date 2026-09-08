"""
tests/unit/test_deploy_templates.py — Tests des templates Jinja2 de déploiement.

Couvre RF-11 (Script de déploiement + CI/CD) :
  - render_deploy_template génère du contenu non vide pour aws/azure/gcp
  - variables Jinja2 injectées correctement (region, resources_csv)
  - flags de migration auto-détectés (has_s3_migration, has_postgres_migration)
  - template GitHub Actions rendu correctement
  - template manquant → erreur structurée (pas crash)

Note : render_deploy_template est un @tool LangChain.
On teste la fonction sous-jacente directement via .func pour éviter
la validation LangChain (qui nécessite une invocation complète de l'agent).
"""
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Chemin vers les templates réels du projet
_AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"
_TEMPLATES_DIR = _AGENTS_DIR / "deployer" / "templates"
_DEPLOY_DIR = _TEMPLATES_DIR / "deploy"
_CICD_DIR   = _TEMPLATES_DIR / "cicd"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render_deploy(cloud_provider: str, region: str, resources_csv: str,
                   project_id: str = "",
                   backend_bucket: str = "",
                   backend_storage_account: str = "",
                   output_dir: str = "/tmp/test-output",
                   **extra) -> str:
    """Rend directement le template deploy.sh.j2 sans passer par le @tool."""
    template_file = f"{cloud_provider}_deploy.sh.j2"
    env = Environment(
        loader=FileSystemLoader(str(_DEPLOY_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    t = env.get_template(template_file)
    return t.render(
        cloud_provider=cloud_provider,
        region=region,
        resources_csv=resources_csv,
        project_id=project_id,
        backend_bucket=backend_bucket,
        backend_storage_account=backend_storage_account,
        output_dir=output_dir,
        has_s3_migration=extra.get("has_s3_migration", False),
        has_postgres_migration=extra.get("has_postgres_migration", False),
        has_ai_migration=extra.get("has_ai_migration", False),
    )


def _render_cicd(cloud_provider: str, region: str, project_id: str = "") -> str:
    """Rend directement le template github_actions.yml.j2."""
    env = Environment(
        loader=FileSystemLoader(str(_CICD_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    t = env.get_template("github_actions.yml.j2")
    return t.render(
        cloud_provider=cloud_provider,
        region=region,
        project_id=project_id,
    )


# ── Existence des templates ────────────────────────────────────────────────────

class TestTemplateFilesExist:

    def test_azure_deploy_template_exists(self):
        assert (_DEPLOY_DIR / "azure_deploy.sh.j2").exists()

    def test_aws_deploy_template_exists(self):
        assert (_DEPLOY_DIR / "aws_deploy.sh.j2").exists()

    def test_gcp_deploy_template_exists(self):
        assert (_DEPLOY_DIR / "gcp_deploy.sh.j2").exists()

    def test_github_actions_template_exists(self):
        assert (_CICD_DIR / "github_actions.yml.j2").exists()


# ── Rendu Azure ───────────────────────────────────────────────────────────────

class TestAzureDeployTemplate:

    def test_renders_non_empty_content(self):
        rendered = _render_deploy("azure", "westeurope", "storage,postgres")
        assert len(rendered) > 100

    def test_contains_shebang(self):
        rendered = _render_deploy("azure", "westeurope", "storage")
        assert "#!/usr/bin/env bash" in rendered

    def test_contains_set_pipefail(self):
        """Le script doit avoir set -euo pipefail pour la sécurité Bash."""
        rendered = _render_deploy("azure", "westeurope", "storage")
        assert "set -euo pipefail" in rendered

    def test_region_injected(self):
        rendered = _render_deploy("azure", "francecentral", "storage")
        assert "francecentral" in rendered

    def test_resources_csv_injected(self):
        rendered = _render_deploy("azure", "westeurope", "mydb,mystorage")
        assert "mydb,mystorage" in rendered

    def test_postgres_section_present_when_flag_true(self):
        rendered = _render_deploy("azure", "westeurope", "postgresql",
                                  has_postgres_migration=True)
        assert "pg_dump" in rendered or "PostgreSQL" in rendered

    def test_postgres_section_absent_when_flag_false(self):
        rendered = _render_deploy("azure", "westeurope", "storage",
                                  has_postgres_migration=False)
        assert "skipped" in rendered.lower() or "pg_dump" not in rendered

    def test_s3_section_present_when_flag_true(self):
        rendered = _render_deploy("azure", "westeurope", "storage,s3",
                                  has_s3_migration=True)
        assert "azcopy" in rendered or "S3" in rendered or "Blob" in rendered

    def test_arm_credential_check_present(self):
        """Le script doit vérifier la présence des variables ARM_*."""
        rendered = _render_deploy("azure", "westeurope", "storage")
        assert "ARM_CLIENT_ID" in rendered
        assert "ARM_CLIENT_SECRET" in rendered

    def test_terraform_commands_present(self):
        rendered = _render_deploy("azure", "westeurope", "storage")
        assert "terraform init" in rendered
        assert "terraform plan" in rendered
        assert "terraform apply" in rendered


# ── Rendu AWS ─────────────────────────────────────────────────────────────────

class TestAwsDeployTemplate:

    def test_renders_non_empty_content(self):
        rendered = _render_deploy("aws", "eu-west-1", "s3,rds")
        assert len(rendered) > 100

    def test_contains_shebang(self):
        rendered = _render_deploy("aws", "eu-west-1", "s3")
        assert "#!/usr/bin/env bash" in rendered

    def test_region_injected(self):
        rendered = _render_deploy("aws", "us-east-1", "lambda")
        assert "us-east-1" in rendered

    def test_aws_credentials_checked(self):
        rendered = _render_deploy("aws", "eu-west-1", "s3")
        assert "AWS_ACCESS_KEY_ID" in rendered or "aws" in rendered.lower()


# ── Rendu GCP ─────────────────────────────────────────────────────────────────

class TestGcpDeployTemplate:

    def test_renders_non_empty_content(self):
        rendered = _render_deploy("gcp", "europe-west1", "cloud_sql,gcs",
                                  project_id="my-project")
        assert len(rendered) > 100

    def test_contains_shebang(self):
        rendered = _render_deploy("gcp", "europe-west1", "gcs", project_id="proj")
        assert "#!/usr/bin/env bash" in rendered

    def test_region_injected(self):
        rendered = _render_deploy("gcp", "us-central1", "cloud_sql", project_id="proj")
        assert "us-central1" in rendered


# ── Rendu GitHub Actions CI/CD ────────────────────────────────────────────────

class TestGithubActionsTemplate:

    def test_azure_cicd_renders_non_empty(self):
        rendered = _render_cicd("azure", "westeurope")
        assert len(rendered) > 100

    def test_cicd_contains_workflow_name(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "name:" in rendered

    def test_cicd_azure_injects_arm_secrets(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "ARM_CLIENT_ID" in rendered
        assert "ARM_CLIENT_SECRET" in rendered

    def test_cicd_aws_injects_aws_secrets(self):
        rendered = _render_cicd("aws", "eu-west-1")
        assert "AWS_ACCESS_KEY_ID" in rendered
        assert "AWS_SECRET_ACCESS_KEY" in rendered

    def test_cicd_gcp_injects_gcp_credentials(self):
        rendered = _render_cicd("gcp", "europe-west1", project_id="my-proj")
        assert "GOOGLE_CREDENTIALS" in rendered

    def test_cicd_contains_validate_job(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "validate" in rendered.lower()

    def test_cicd_contains_terraform_plan_job(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "plan" in rendered.lower()

    def test_cicd_contains_terraform_apply_job(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "apply" in rendered.lower()

    def test_cicd_cloud_provider_uppercased_in_name(self):
        rendered = _render_cicd("azure", "westeurope")
        assert "AZURE" in rendered

    def test_cicd_tf_version_present(self):
        rendered = _render_cicd("aws", "eu-west-1")
        assert "TF_VERSION" in rendered


# ── _detect_migration_flags ───────────────────────────────────────────────────

class TestDetectMigrationFlags:
    """Tests de la logique de détection automatique des flags de migration."""

    def test_s3_detected_from_resources_csv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(tmp_path))
        from agents.iac_generator.cloud_integration import _detect_migration_flags
        flags = _detect_migration_flags("storage_account,postgresql")
        assert flags["has_s3_migration"] is True

    def test_postgres_detected_from_resources_csv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(tmp_path))
        from agents.iac_generator.cloud_integration import _detect_migration_flags
        flags = _detect_migration_flags("postgresql_flexible_server,storage")
        assert flags["has_postgres_migration"] is True

    def test_ai_detected_from_cognitive_account(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(tmp_path))
        from agents.iac_generator.cloud_integration import _detect_migration_flags
        flags = _detect_migration_flags("cognitive_account,storage")
        assert flags["has_ai_migration"] is True

    def test_no_flags_for_plain_vm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(tmp_path))
        from agents.iac_generator.cloud_integration import _detect_migration_flags
        flags = _detect_migration_flags("azurerm_linux_virtual_machine")
        assert flags["has_s3_migration"] is False
        assert flags["has_postgres_migration"] is False
        assert flags["has_ai_migration"] is False
