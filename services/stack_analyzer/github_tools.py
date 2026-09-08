"""
github_tools.py — PyGithub utilities for stack_analyzer.py

Self-contained: no shared/ dependency.
"""
import json
import logging
import os
from typing import Any

logger = logging.getLogger("Tools")

try:
    from github import Github as _Github
    from github import GithubException as _GithubException
    _HAS_PYGITHUB = True
except ImportError:
    _Github = None          # type: ignore[assignment,misc]
    _GithubException = Exception  # type: ignore[assignment,misc]
    _HAS_PYGITHUB = False


def _extract_python_from_notebook(content: str) -> str:
    """Extract code cells from a .ipynb notebook as Python source."""
    try:
        nb = json.loads(content)
        cells = []
        for cell in nb.get("cells", []):
            if cell.get("cell_type") == "code":
                source = cell.get("source", [])
                code = "".join(source) if isinstance(source, list) else source
                clean = "\n".join(
                    line for line in code.splitlines()
                    if not line.strip().startswith(("%", "!"))
                )
                cells.append(clean)
        return "\n\n".join(cells)
    except Exception as exc:
        logger.warning(f"_extract_python_from_notebook: {exc}")
        return ""


def _pygithub_fetch_file(
    owner: str, repo: str, file_path: str,
    branch: str = "main", github_token: str = "",
) -> str:
    """Fetch file content from GitHub via PyGithub."""
    if not _HAS_PYGITHUB:
        return ""
    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return ""
    try:
        g = _Github(token)
        repository = g.get_repo(f"{owner}/{repo}")
        content_file = repository.get_contents(file_path, ref=branch)
        if hasattr(content_file, "decoded_content"):
            return content_file.decoded_content.decode("utf-8")
        return ""
    except _GithubException as exc:
        logger.debug(f"_pygithub_fetch_file({file_path}): {exc}")
        return ""
    except Exception as exc:
        logger.debug(f"_pygithub_fetch_file({file_path}): unexpected: {exc}")
        return ""


def _pygithub_list_files(
    owner: str, repo: str, branch: str = "main",
    github_token: str = "", extensions: tuple[str, ...] = (".py", ".ipynb"),
) -> list[str]:
    """List files in a GitHub repo via PyGithub Git Trees API (recursive)."""
    if not _HAS_PYGITHUB:
        return []
    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return []
    try:
        g = _Github(token)
        repository = g.get_repo(f"{owner}/{repo}")
        tree = repository.get_git_tree(sha=branch, recursive=True)
        return [
            item.path for item in tree.tree
            if item.type == "blob" and item.path.endswith(extensions)
        ]
    except _GithubException as exc:
        logger.debug(f"_pygithub_list_files({owner}/{repo}): {exc}")
        return []
    except Exception as exc:
        logger.debug(f"_pygithub_list_files: unexpected: {exc}")
        return []


def _pygithub_clone_info(repo_url: str, github_token: str = "") -> dict[str, Any]:
    """Parse GitHub URL and return repo metadata (owner, repo, branch)."""
    if not _HAS_PYGITHUB:
        return {"status": "error", "detail": "PyGithub not available"}

    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"status": "error", "detail": "No GitHub token"}

    try:
        parts = repo_url.rstrip("/").split("/")
        owner = parts[-2]
        repo = parts[-1].replace(".git", "")

        g = _Github(token)
        gh_repo = g.get_repo(f"{owner}/{repo}")
        branch = gh_repo.default_branch

        logger.info(f"Repo accessible: {owner}/{repo} (branch: {branch})")
        return {
            "status": "success",
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "repo_url": repo_url,
        }
    except _GithubException as exc:
        logger.warning(f"_pygithub_clone_info: {exc}")
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:
        logger.error(f"_pygithub_clone_info: {exc}")
        return {"status": "error", "detail": str(exc)}


def _get_current_sha(repo_url: str, github_token: str = "") -> str | None:
    """Return the HEAD commit SHA of the repo's default branch.

    Used by Path 2 (incremental) to compare with the last analyzed SHA.
    Returns None on any error so callers can fall back to Path 1 safely.
    """
    if not _HAS_PYGITHUB:
        return None
    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return None
    try:
        parts = repo_url.rstrip("/").split("/")
        owner, repo = parts[-2], parts[-1].replace(".git", "")
        g = _Github(token)
        gh_repo = g.get_repo(f"{owner}/{repo}")
        branch = gh_repo.default_branch
        sha = gh_repo.get_branch(branch).commit.sha
        logger.debug("_get_current_sha: %s/%s@%s = %s", owner, repo, branch, sha[:8])
        return sha
    except Exception as exc:
        logger.warning("_get_current_sha: %s — %s", repo_url, exc)
        return None


def _get_repo_diff(
    repo_url: str,
    previous_sha: str,
    current_sha: str,
    github_token: str = "",
) -> dict[str, Any]:
    """Return a structured diff between two commits.

    Returns:
        {
          added_files:    list[str]  — new files (filename only)
          modified_files: list[str]  — changed files
          removed_files:  list[str]  — deleted files
          new_resources:  list[str]  — cloud resource types detected in added .tf / .py files
          has_changes:    bool
        }
    On any error returns has_changes=True so the pipeline falls back to Path 1 safely.
    """
    _RESOURCE_PREFIXES = ("aws_", "azurerm_", "google_", "oci_")
    _IaC_EXTS = (".tf", ".bicep", ".yaml", ".yml")
    _CODE_EXTS = (".py", ".ipynb", ".txt", ".env", ".json")

    if not _HAS_PYGITHUB:
        return {"has_changes": True, "error": "PyGithub not available"}

    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"has_changes": True, "error": "No GitHub token"}

    try:
        parts = repo_url.rstrip("/").split("/")
        owner, repo = parts[-2], parts[-1].replace(".git", "")
        g = _Github(token)
        gh_repo = g.get_repo(f"{owner}/{repo}")

        comparison = gh_repo.compare(previous_sha, current_sha)
        added_files, modified_files, removed_files = [], [], []
        new_resources: list[str] = []

        for f in comparison.files:
            name = f.filename
            status = f.status  # "added" | "modified" | "removed" | "renamed"
            if status == "added":
                added_files.append(name)
                # Try to detect new cloud resources in added .tf files
                if name.endswith(".tf") and f.patch:
                    for line in f.patch.splitlines():
                        if line.startswith("+") and "resource" in line:
                            for prefix in _RESOURCE_PREFIXES:
                                import re
                                m = re.search(rf'"{prefix}[\w]+"', line)
                                if m:
                                    new_resources.append(m.group().strip('"'))
            elif status in ("modified", "renamed"):
                modified_files.append(name)
            elif status == "removed":
                removed_files.append(name)

        has_changes = bool(added_files or modified_files or removed_files)
        result = {
            "has_changes": has_changes,
            "added_files": added_files,
            "modified_files": modified_files,
            "removed_files": removed_files,
            "new_resources": list(set(new_resources)),
            "previous_sha": previous_sha,
            "current_sha": current_sha,
            "total_files_changed": len(added_files) + len(modified_files) + len(removed_files),
        }
        logger.info(
            "_get_repo_diff: +%d ~%d -%d files, %d new resources detected",
            len(added_files), len(modified_files), len(removed_files), len(new_resources),
        )
        return result

    except Exception as exc:
        logger.warning("_get_repo_diff: %s — %s (falling back to full)", repo_url, exc)
        return {"has_changes": True, "error": str(exc)}


def _pygithub_list_infra_files(
    owner: str, repo: str, branch: str = "main",
    github_token: str = "",
) -> dict[str, Any]:
    """List infrastructure files in a GitHub repo (Terraform, Docker, etc.)."""
    if not _HAS_PYGITHUB:
        return {"terraform": [], "docker_compose": [], "dockerfile": [], "env": []}

    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"terraform": [], "docker_compose": [], "dockerfile": [], "env": []}

    result: dict[str, list] = {
        "terraform": [],
        "docker_compose": [],
        "dockerfile": [],
        "env": [],
        "bicep": [],
        "cloudformation": [],
        "arm": [],
        "helm": [],         # list of chart root directories (contain Chart.yaml)
    }

    try:
        g = _Github(token)
        repository = g.get_repo(f"{owner}/{repo}")
        tree = repository.get_git_tree(sha=branch, recursive=True)

        helm_chart_dirs: set[str] = set()

        for item in tree.tree:
            if item.type != "blob":
                continue
            path = item.path
            filename = path.split("/")[-1].lower()

            if path.endswith(".tf"):
                result["terraform"].append(path)
            elif path.endswith(".tfvars"):
                result.setdefault("tfvars", []).append(path)
            elif path.endswith(".bicep"):
                result["bicep"].append(path)
            elif filename in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                result["docker_compose"].append(path)
            elif filename == "dockerfile":
                result["dockerfile"].append(path)
            elif filename in (".env", ".env.example", ".env.sample"):
                result["env"].append(path)
            elif filename == "chart.yaml":
                # The chart root is the parent directory of Chart.yaml
                chart_dir = "/".join(path.split("/")[:-1]) if "/" in path else "."
                helm_chart_dirs.add(chart_dir)
            elif path.endswith(".json") and any(
                kw in filename for kw in ("azuredeploy", "arm", "template", "maintemplate")
            ):
                result["arm"].append(path)
            elif filename in ("azuredeploy.json", "maintemplate.json"):
                result["arm"].append(path)
            elif path.endswith((".yaml", ".yml")) and filename not in (
                "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"
            ):
                result["cloudformation"].append(path)

        result["helm"] = sorted(helm_chart_dirs)

        total = sum(len(v) for v in result.values())
        logger.info(f"_pygithub_list_infra_files: {total} infra files in {owner}/{repo}")
        return result
    except _GithubException as exc:
        logger.warning(f"_pygithub_list_infra_files: {exc}")
        return result
    except Exception as exc:
        logger.error(f"_pygithub_list_infra_files: {exc}")
        return result


def fetch_file_content(
    owner: str, repo: str, file_path: str, branch: str = "main", github_token: str = ""
) -> str:
    """Fetch raw file content from GitHub; auto-extracts code from .ipynb files."""
    token = github_token or os.environ.get("GITHUB_TOKEN", "")
    if _HAS_PYGITHUB:
        content = _pygithub_fetch_file(owner, repo, file_path, branch, token)
        if content:
            if file_path.endswith(".ipynb"):
                content = _extract_python_from_notebook(content)
            return content
    logger.warning(f"fetch_file_content: Cannot fetch {file_path} (PyGithub not available)")
    return ""
