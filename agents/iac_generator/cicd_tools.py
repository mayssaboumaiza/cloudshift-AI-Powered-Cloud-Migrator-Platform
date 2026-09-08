"""
cicd_tools.py — CI/CD pipeline file generation for automated Terraform deployments.

Single LangChain tool:
  generate_cicd_pipeline — Render a GitHub Actions or GitLab CI pipeline file
                           from a Jinja2 template, then write it to the output directory.

Self-contained: only standard library + jinja2 + langchain_core.
"""

from __future__ import annotations

import json
import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

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


# ── Tool ──────────────────────────────────────────────────────────────────────

@tool
def generate_cicd_pipeline(
    cloud_provider: str,
    region: str,
    pipeline_type: str,
    project_id: str = "",
) -> str:
    """Generate a CI/CD pipeline file for automated deployments.

    Produces a ready-to-commit pipeline configuration using Jinja2 templates
    with provider-specific credential secrets, a Checkov scan step, and
    plan/apply jobs separated by manual approval.

    Args:
        cloud_provider: One of 'aws', 'gcp', 'azure'.
        region:         Target deployment region.
        pipeline_type:  One of 'github_actions', 'gitlab_ci'.
        project_id:     GCP project ID (required for GCP only).

    Returns:
        JSON string with: filename (str), saved_to (str), secrets_needed (list[str]),
        content (str).  On error: {"error": "..."}.
    """
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        cicd_templates_dir = os.path.join(TEMPLATES_DIR, "cicd")
        template_map = {
            "github_actions": ("github_actions.yml.j2", ".github/workflows/deploy.yml"),
            "gitlab_ci":      ("gitlab_ci.yml.j2",      ".gitlab-ci.yml"),
        }

        if pipeline_type not in template_map:
            return json.dumps({
                "error": (
                    f"Unknown pipeline_type '{pipeline_type}'. "
                    f"Supported: {list(template_map.keys())}"
                )
            })

        template_file, output_filename = template_map[pipeline_type]
        template_path = os.path.join(cicd_templates_dir, template_file)

        if not os.path.exists(template_path):
            logger.warning(f"generate_cicd_pipeline: template not found: {template_path}")
            return json.dumps({"error": f"Template not found: {template_file}"})

        env = Environment(
            loader=FileSystemLoader(cicd_templates_dir),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = env.get_template(template_file)
        rendered = template.render(
            cloud_provider=cloud_provider,
            region=region,
            project_id=project_id or "",
        )

        secrets_map = {
            "aws":   ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "gcp":   ["GOOGLE_CREDENTIALS"],
            "azure": ["ARM_CLIENT_ID", "ARM_CLIENT_SECRET", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID"],
        }
        secrets_needed = secrets_map.get(cloud_provider, [])

        output_dir = _get_output_dir()
        out_path = os.path.join(output_dir, os.path.basename(output_filename))
        os.makedirs(output_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(rendered)

        logger.info(f"generate_cicd_pipeline: generated {output_filename} → {out_path}")
        return json.dumps({
            "filename":       output_filename,
            "saved_to":       out_path,
            "secrets_needed": secrets_needed,
            "content":        rendered,
        })

    except Exception as e:
        logger.error(f"generate_cicd_pipeline failed: {e}")
        return json.dumps({"error": str(e)})
