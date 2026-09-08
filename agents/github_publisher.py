"""
github_publisher.py — Publish generated IaC to a new GitHub repo and open a PR.

Flow:
  1. Create a new private repo: <source-repo>-<target-cloud>-migration
  2. Push all .tf files + deploy.sh + .github/workflows/deploy.yml to branch feat/cloud-migration
  3. Open a PR: feat/cloud-migration → main (user reviews + merges)
  4. Return {"pr_url": str, "repo_url": str, "error": str | None}
"""
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("GitHubPublisher")


def _with_retry(fn, *args, max_attempts: int = 3, base_delay: float = 2.0, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on transient GitHub errors.

    Retries on: ConnectionError, socket timeout, HTTP 500/502/503/504, rate-limit (403/429).
    Raises immediately on: 401 (bad token), 404 (not found), 422 (validation).
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            # Non-retryable: auth/validation errors
            status = getattr(exc, "status", None)
            if status in (401, 404, 422):
                raise
            if attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))  # 2s, 4s, 8s
            logger.warning(
                "GitHubPublisher: transient error on attempt %d/%d (%s) — retrying in %.0fs",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)

_PROJECT_ROOT = Path(__file__).parent.parent
def _get_output_dir() -> Path:
    """Re-read MIGRATION_OUTPUT_DIR so publisher uses the per-migration path set by Agent 02."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    if _out_env and os.path.isabs(_out_env):
        return Path(_out_env)
    return _PROJECT_ROOT / (_out_env or os.path.join("output", "migrated_app"))

try:
    from github import Github, GithubException
    _HAS_PYGITHUB = True
except ImportError:
    _HAS_PYGITHUB = False


def _resolve_token(state: dict) -> str:
    """Return the GitHub token from state (already plaintext — decryption handled upstream)."""
    return state.get("github_token") or os.environ.get("GITHUB_TOKEN", "")


def _source_repo_name(state: dict) -> str:
    """Extract the bare repo name from the first repo URL in state."""
    urls = state.get("repo_urls") or []
    url = urls[0] if urls else (state.get("repo_url") or "")
    if not url:
        return "infra"
    parts = url.rstrip("/").replace("https://github.com/", "").split("/")
    return parts[-1] if parts else "infra"


def _fetch_source_readme(token: str, state: dict) -> str:
    """Fetch the README.md from the source GitHub repo. Returns empty string if unavailable."""
    urls = state.get("repo_urls") or []
    url = urls[0] if urls else (state.get("repo_url") or "")
    if not url or not _HAS_PYGITHUB:
        return ""
    try:
        g = Github(token)
        repo_path = url.rstrip("/").replace("https://github.com/", "").split(".git")[0]
        repo = g.get_repo(repo_path)
        contents = repo.get_contents("README.md")
        import base64 as _b64
        return _b64.b64decode(contents.content).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("GitHubPublisher: could not fetch source README: %s", exc)
        return ""


def _build_migration_section(state: dict) -> str:
    """Build the migration section to append to the source README."""
    plan = state.get("migration_plan") or {}
    summary = plan.get("summary") or {}
    resources = plan.get("resources") or []
    source_cloud = (state.get("source_cloud") or "source").upper()
    target_cloud = (state.get("target_cloud") or "cloud").upper().replace("AZURERM", "AZURE")
    target_region = state.get("target_region") or "N/A"
    cost = summary.get("estimated_total_monthly")
    dep_status = state.get("deployment_status", "unknown")

    lines = [
        "",
        "---",
        "",
        "## ⚡ Cloud Migration — Infrastructure as Code",
        "",
        "> Ce repo a été enrichi automatiquement par [Cloud Migrator](https://github.com/mayssaboumaiza/cloud-migrator) — pipeline IA de migration cloud.",
        "",
        "### Vue d'ensemble",
        "",
        "| Champ | Valeur |",
        "|-------|--------|",
        f"| Source | `{source_cloud}` |",
        f"| Cible | `{target_cloud}` |",
        f"| Région | `{target_region}` |",
        f"| Services migrés | {len(resources)} |",
    ]
    if cost:
        lines.append(f"| Coût estimé | ~${cost:.0f}/mois |")
    lines += [
        f"| Statut déploiement | `{dep_status}` |",
        "",
        "### Fichiers Terraform générés",
        "",
        "| Fichier | Rôle |",
        "|---------|------|",
        "| `provider.tf` | Configuration du provider Azure + contraintes de version |",
        "| `variables.tf` | Variables d'entrée (région, noms, tailles SKU…) |",
        "| `network.tf` | Réseau virtuel, sous-réseaux, NSG |",
        "| `storage.tf` | Comptes de stockage Azure Blob |",
        "| `database.tf` | Base de données PostgreSQL Flexible Server |",
        "| `iam.tf` | Identités managées, RBAC, rôles |",
        "| `ai.tf` | Azure Machine Learning / Cognitive Services |",
        "| `outputs.tf` | Sorties Terraform (FQDN, noms, endpoints) |",
        "",
    ]

    if resources:
        lines += [
            "### Plan de migration (7R)",
            "",
            "| Service source | Stratégie | Service cible |",
            "|----------------|-----------|---------------|",
        ]
        for r in resources[:20]:
            src = r.get("source_service") or r.get("resource_name", "")
            tgt = r.get("target_service") or r.get("target_equivalent") or r.get("terraform_resource", "")
            strat = r.get("strategy") or r.get("strategy_7r", "")
            lines.append(f"| `{src}` | {strat} | `{tgt}` |")
        if len(resources) > 20:
            lines.append(f"| *+{len(resources)-20} autres services* | | |")
        lines.append("")

    lines += [
        "### Démarrage rapide",
        "",
        "```bash",
        "export ARM_CLIENT_ID=<votre-client-id>",
        "export ARM_TENANT_ID=<votre-tenant-id>",
        "export ARM_CLIENT_SECRET=<votre-client-secret>",
        "export ARM_SUBSCRIPTION_ID=<votre-subscription-id>",
        "terraform init && terraform plan && terraform apply",
        "```",
        "",
        "Ou via le script généré : `bash deploy.sh`",
        "",
        f"*Généré par Cloud Migrator — {source_cloud} → {target_cloud} — {target_region}*",
    ]
    return "\n".join(lines)


def _build_readme(state: dict, token: str = "") -> str:
    """Return the source README with the migration section appended.

    If the source README cannot be fetched, falls back to a standalone migration README.
    Strips any previously generated migration section (idempotent on re-run).
    """
    _MIGRATION_ANCHOR = "## ⚡ Cloud Migration — Infrastructure as Code"

    source_readme = _fetch_source_readme(token, state) if token else ""

    if source_readme:
        # Remove any previously appended migration section (idempotent)
        anchor_idx = source_readme.find(_MIGRATION_ANCHOR)
        if anchor_idx != -1:
            # Walk back to include the preceding "---\n" separator if present
            sep_idx = source_readme.rfind("\n---\n", 0, anchor_idx)
            if sep_idx != -1:
                source_readme = source_readme[:sep_idx]
            else:
                source_readme = source_readme[:anchor_idx]
        source_readme = source_readme.rstrip()
        return source_readme + _build_migration_section(state)

    # Fallback: no source README available — build a standalone one
    plan = state.get("migration_plan") or {}
    resources = plan.get("resources") or []
    source_name = _source_repo_name(state)
    source_cloud = (state.get("source_cloud") or "source").upper()
    target_cloud = (state.get("target_cloud") or "cloud").upper().replace("AZURERM", "AZURE")
    target_region = state.get("target_region") or "N/A"

    header = "\n".join([
        f"# {source_name} — Infrastructure Cloud ({source_cloud} → {target_cloud})",
        "",
    ])
    return header + _build_migration_section(state)


def _build_pr_body(state: dict) -> str:
    plan = state.get("migration_plan") or {}
    summary = plan.get("summary") or {}
    resources = plan.get("resources") or []
    source_cloud = (state.get("source_cloud") or "source").upper()
    target_cloud = (state.get("target_cloud") or "cloud").upper()

    dep_status = state.get("deployment_status", "unknown")
    deploy_icon = "✅" if dep_status == "deployed" else ("⚠️" if dep_status in ("blocked", "failed") else "📋")
    deploy_note = {
        "deployed": "Infrastructure successfully deployed to the cloud target.",
        "failed":   "⚠️ `terraform apply` failed — review the errors below and re-run after fixing.",
        "blocked":  "⚠️ Deployment was blocked (missing credentials or config issue) — check deploy.sh.",
    }.get(dep_status, "Deployment status: " + dep_status)

    lines = [
        f"## Cloud Migration: {source_cloud} → {target_cloud}",
        "",
        f"{deploy_icon} **Deployment status**: {deploy_note}",
        "",
        "This PR was **automatically generated** by Cloud Migrator.",
        "It contains all Terraform IaC files needed to deploy your migrated infrastructure.",
        "",
        "### Summary",
        f"- **Services migrated**: {len(resources)}",
        f"- **Target region**: {state.get('target_region', 'N/A')}",
    ]

    cost = summary.get("estimated_total_monthly")
    if cost:
        lines.append(f"- **Estimated monthly cost**: ${cost:.0f}/mo")

    if resources:
        lines += [
            "",
            "### Migration map",
            "| Source service | Strategy | Target service | Score |",
            "|----------------|----------|----------------|-------|",
        ]
        for r in resources[:25]:
            src = r.get("source_service") or r.get("resource_name", "")
            tgt = r.get("target_service") or r.get("target_equivalent") or r.get("terraform_resource", "")
            strat = r.get("strategy") or r.get("strategy_7r", "")
            sc = r.get("score") or r.get("equivalence_score")
            sc_str = f"{sc*100:.0f}%" if sc is not None else "—"
            lines.append(f"| `{src}` | {strat} | `{tgt}` | {sc_str} |")
        if len(resources) > 25:
            lines.append(f"| *+{len(resources)-25} more* | | | |")

    lines += [
        "",
        "### Files in this PR",
        "| File | Description |",
        "|------|-------------|",
        "| `*.tf` | Terraform modules (provider, compute, storage, network, database, IAM) |",
        "| `deploy.sh` | Deployment script: terraform fmt → init → plan → apply |",
        "| `.github/workflows/deploy.yml` | GitHub Actions CI/CD pipeline |",
        "",
        "### How to proceed",
        "1. **Review** the Terraform files — check resources, tags, and naming conventions",
        "2. **Configure secrets** in *Settings → Secrets* (see env vars in `deploy.sh`)",
        "3. **Run locally** `terraform plan` to confirm no unexpected changes",
        "4. **Merge** when ready — GitHub Actions will validate automatically",
        "",
        "> ⚠️ Do NOT merge without reviewing the plan output.",
        "  `auto_approve` is disabled — a manual `terraform apply` is required after merging.",
        "",
        "---",
        "*Generated by Cloud Migrator — AI-Powered Cloud Migration Pipeline*",
    ]
    return "\n".join(lines)


def _build_atlantis_yaml(state: dict) -> str:
    """Generate atlantis.yaml for the pushed repo.

    Atlantis reads this file to know which directory to run terraform in,
    what approval is required, and what workflow to execute.
    The MIGRATION_CALLBACK_URL run step is optional — remove if no callback needed.
    """
    migration_id = state.get("migration_id", "unknown")
    return f"""version: 3
projects:
- name: cloud-migration
  dir: .
  workspace: default
  autoplan:
    when_modified: ["*.tf", "*.tfvars"]
    enabled: true
  apply_requirements:
  - approved
  workflow: cloud-migrator

workflows:
  cloud-migrator:
    plan:
      steps:
      - init
      - plan
    apply:
      steps:
      - apply
      - run: >-
          curl -sf -X POST "${{MIGRATION_CALLBACK_URL:-}}"
          -H "Content-Type: application/json"
          -d '{{"status":"applied","migration_id":"{migration_id}"}}'
          || echo "Callback skipped (MIGRATION_CALLBACK_URL not set)"
"""


def _fetch_cloud_credentials(state: dict) -> dict:
    """Fetch cloud credentials from Vault using the migration_id stored in state.

    Returns a flat dict ready to map to GitHub Actions secrets.
    Falls back to empty dict if Vault is unavailable (non-blocking).
    """
    migration_id = state.get("migration_id", "")
    target_cloud = (state.get("target_cloud") or "").lower()
    if not migration_id:
        return {}
    try:
        from services.credentials.broker import get_secret_broker, SecretBroker
        broker = get_secret_broker()

        if "azure" in target_cloud or "azurerm" in target_cloud:
            data = broker.get(SecretBroker.migration_path(migration_id, "azure")) or {}
            return {
                "ARM_CLIENT_ID":       data.get("client_id", ""),
                "ARM_CLIENT_SECRET":   data.get("client_secret", ""),
                "ARM_TENANT_ID":       data.get("tenant_id", ""),
                "ARM_SUBSCRIPTION_ID": data.get("subscription_id", ""),
            }
        elif "aws" in target_cloud:
            data = broker.get(SecretBroker.migration_path(migration_id, "aws")) or {}
            return {
                "AWS_ACCESS_KEY_ID":     data.get("access_key", ""),
                "AWS_SECRET_ACCESS_KEY": data.get("secret_key", ""),
                "AWS_DEFAULT_REGION":    data.get("region", ""),
            }
        elif "gcp" in target_cloud or "google" in target_cloud:
            data = broker.get(SecretBroker.migration_path(migration_id, "gcp")) or {}
            import json as _json
            sa = data.get("service_account_json")
            return {"GOOGLE_CREDENTIALS": _json.dumps(sa) if sa else ""}
    except Exception as exc:
        logger.warning("GitHubPublisher: could not fetch cloud credentials from Vault: %s", exc)
    return {}


def _inject_github_actions_secrets(repo, state: dict) -> None:
    """Push per-migration cloud credentials as GitHub Actions secrets.

    Called after repo creation so CI/CD pipelines can run terraform apply
    without any manual secret configuration in the GitHub UI.
    Credentials come from Vault (fetched JIT) — never from env vars or state dict.
    Non-blocking: logs a warning on failure and continues.
    """
    creds = _fetch_cloud_credentials(state)
    if not creds:
        logger.info("GitHubPublisher: no cloud credentials to push as GH Actions secrets")
        return

    pushed, skipped = [], []
    for name, value in creds.items():
        if not value:
            skipped.append(name)
            continue
        try:
            repo.create_secret(name, value)
            pushed.append(name)
        except Exception as exc:
            logger.warning("GitHubPublisher: could not set secret %s: %s", name, exc)
            skipped.append(name)

    if pushed:
        logger.info(
            "GitHubPublisher: %d GitHub Actions secret(s) configured automatically: %s",
            len(pushed), pushed,
        )
    if skipped:
        logger.warning(
            "GitHubPublisher: %d secret(s) skipped (empty or error): %s", len(skipped), skipped
        )


def publish_to_github(state: dict) -> dict:
    """
    Create a new GitHub repo, push all generated IaC files to a feature branch,
    and open a PR for the user to review and merge.

    Returns {"pr_url": str, "repo_url": str, "error": str | None}
    """
    if not _HAS_PYGITHUB:
        return {"pr_url": "", "repo_url": "", "error": "PyGithub not installed"}

    token = _resolve_token(state)
    if not token:
        return {"pr_url": "", "repo_url": "", "error": "No GitHub token available"}

    try:
        g = Github(token)
        user = g.get_user()

        # ── 1. Determine new repo name ──────────────────────────────────────
        source_name = _source_repo_name(state)
        target_cloud = (state.get("target_cloud") or "cloud").lower().replace("azurerm", "azure")
        new_repo_name = f"{source_name}-{target_cloud}-migration"

        # ── 2. Create or reuse the repo ─────────────────────────────────────
        try:
            new_repo = user.create_repo(
                name=new_repo_name,
                description=f"Terraform IaC generated by Cloud Migrator ({source_name} → {target_cloud})",
                private=True,
                auto_init=True,   # creates default branch with an initial README commit
            )
            logger.info(f"GitHubPublisher: created repo {new_repo.full_name}")
        except GithubException as exc:
            if exc.status == 422:  # already exists
                new_repo = user.get_repo(new_repo_name)
                logger.info(f"GitHubPublisher: reusing existing repo {new_repo.full_name}")
            else:
                raise

        # ── 3. Collect ALL files from OUTPUT_DIR ────────────────────────────
        # Push everything Agent 02 and Agent 03 generated: .tf, .py, .sh,
        # .env, requirements.txt, deploy.yml, etc.
        # Exclude only Terraform runner-managed state/lock files (they must
        # not be committed — they contain secrets and live on the runner only).
        output_dir = _get_output_dir()
        files_to_push: dict[str, str] = {}

        _EXCLUDE_FILENAMES = {
            "terraform.tfstate", "terraform.tfstate.backup",
            ".terraform.lock.hcl",
            # debug dumps — never push to client repo
            "agent_02_messages_debug.json", "agent_03_messages_debug.json",
            # binary Terraform plan — not human-readable
            "tfplan.binary",
            # infra health snapshot — internal only
            "infra_health.json",
            # README is rebuilt below — skip any raw version from output dir
            "README.md",
        }
        _EXCLUDE_DIRS = {".terraform", "__pycache__", ".git"}

        # Rename map: AWS-named Python files → Azure-named equivalents
        _RENAME_MAP = {
            "s3.py":  "blob.py",
            "llm.py": "openai_client.py",
        }

        if output_dir.is_dir():
            for item in sorted(output_dir.rglob("*")):
                if not item.is_file():
                    continue
                # Skip excluded dirs anywhere in the path
                if any(part in _EXCLUDE_DIRS for part in item.parts):
                    continue
                # Skip excluded filenames
                if item.name in _EXCLUDE_FILENAMES:
                    continue
                # Skip files larger than 500 KB (binaries, compiled artifacts)
                try:
                    if item.stat().st_size > 500_000:
                        continue
                except OSError:
                    continue
                rel_path = item.relative_to(output_dir).as_posix()
                # Apply rename: keep directory structure, rename only the filename
                parts = rel_path.rsplit("/", 1)
                filename = parts[-1]
                if filename in _RENAME_MAP:
                    renamed = _RENAME_MAP[filename]
                    rel_path = (parts[0] + "/" + renamed) if len(parts) > 1 else renamed
                    logger.info("GitHubPublisher: renaming %s → %s", filename, renamed)
                try:
                    files_to_push[rel_path] = item.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue  # skip unreadable files (binary assets)

        # Always include atlantis.yaml so Atlantis auto-plans on PR open
        files_to_push["atlantis.yaml"] = _build_atlantis_yaml(state)

        # Single README.md at repo root — source README + migration section appended.
        # Falls back to a standalone migration README if the source cannot be fetched.
        files_to_push["README.md"] = _build_readme(state, token=token)

        if not files_to_push:
            logger.warning("GitHubPublisher: no generated files found — skipping")
            return {"pr_url": "", "repo_url": new_repo.html_url, "error": "No files to push"}

        # ── 4. Create feature branch from default branch ────────────────────
        branch_name = "feat/cloud-migration"
        default_branch = new_repo.default_branch
        main_sha = _with_retry(lambda: new_repo.get_branch(default_branch).commit.sha)

        try:
            _with_retry(new_repo.create_git_ref, ref=f"refs/heads/{branch_name}", sha=main_sha)
            logger.info(f"GitHubPublisher: created branch {branch_name}")
        except GithubException as exc:
            if exc.status == 422:  # branch already exists
                logger.info(f"GitHubPublisher: branch {branch_name} already exists — reusing")
            else:
                raise

        # ── 5. Push each file (create or update) ────────────────────────────
        for file_path, content in files_to_push.items():
            try:
                existing = _with_retry(new_repo.get_contents, file_path, ref=branch_name)
                _with_retry(
                    new_repo.update_file,
                    path=file_path,
                    message=f"chore: update {file_path}",
                    content=content,
                    sha=existing.sha,
                    branch=branch_name,
                )
            except GithubException as exc:
                if exc.status == 404:
                    _with_retry(
                        new_repo.create_file,
                        path=file_path,
                        message=f"feat: add {file_path}",
                        content=content,
                        branch=branch_name,
                    )
                else:
                    raise

        logger.info(
            f"GitHubPublisher: pushed {len(files_to_push)} file(s) to {branch_name}"
        )

        # ── 6. Inject GitHub Actions secrets from Vault (per-migration creds) ──
        _inject_github_actions_secrets(new_repo, state)

        # ── 7. Open the PR ───────────────────────────────────────────────────
        pr_title = (
            f"feat: migrate {source_name} to {target_cloud.upper()} "
            f"({len(files_to_push)} files)"
        )
        try:
            pr = _with_retry(
                new_repo.create_pull,
                title=pr_title,
                body=_build_pr_body(state),
                head=branch_name,
                base=default_branch,
            )
            logger.info(f"GitHubPublisher: PR opened → {pr.html_url}")
            return {"pr_url": pr.html_url, "repo_url": new_repo.html_url, "error": None}
        except GithubException as exc:
            if exc.status == 422:  # PR already open
                existing_prs = list(
                    new_repo.get_pulls(state="open", head=f"{user.login}:{branch_name}")
                )
                if existing_prs:
                    return {
                        "pr_url": existing_prs[0].html_url,
                        "repo_url": new_repo.html_url,
                        "error": None,
                    }
            raise

    except Exception as exc:
        logger.error(f"GitHubPublisher: failed — {exc}")
        return {"pr_url": "", "repo_url": "", "error": str(exc)}
