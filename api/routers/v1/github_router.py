"""
github_router.py — GitHub entry layer.

Endpoints:
  POST /github/analyze  — validates, detects, clones, passes to stack_analyzer
  GET  /github/repos    — lists available repos for the React interface

These two endpoints precede the existing LangGraph pipeline.
They do not touch stack_analyzer, Agent 01, or the database.
"""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from api.schemas.migration_schema import (
    GitHubAnalyzeRequest,
    GitHubAnalyzeResponse,
    ServiceInfo,
)
from services.github.github_validator import validate_github_token
from services.github.account_detector import detect_account_type
from services.github.repo_lister import list_repos
from services.github.case_detector import detect_case
from services.github.repo_cloner import clone_repos

logger = logging.getLogger("GitHubRouter")

router = APIRouter(prefix="/github", tags=["GITHUB"])


# ── POST /github/analyze ─────────────────────────────────────────────────────

@router.post(
    "/analyze",
    response_model=GitHubAnalyzeResponse,
    status_code=status.HTTP_200_OK,
)
async def analyze(body: GitHubAnalyzeRequest):
    """Migration pipeline entry point.

    Steps:
      1. Validates the GitHub token (401 if invalid)
      2. Detects account type (org / user) from the URL
      3. Detects architecture case (CASE B / CASE C)
      4. Shallow-clones repos into /tmp/migrator/
      5. Passes payload to stack_analyzer and returns its result

    The token never appears in logs.
    Budget / region / timeline fields are passed as-is to stack_analyzer.
    """
    token = body.github_token

    validate_github_token(token)
    account = detect_account_type(token, body.github_url)

    detection = detect_case(token, body.selected_repos)
    case = detection["case"]
    services = detection["services"]

    services_with_paths = clone_repos(token, services)

    migration_context: dict[str, Any] = {
        "cloud_source": body.cloud_source,
        "cloud_target": body.cloud_target,
        "budget_max": body.budget_max,
        "target_region": body.target_region,
        "data_residency": body.data_residency,
    }

    analysis = _call_stack_analyzer(
        token=token,
        services=services_with_paths,
        migration_context=migration_context,
    )

    return GitHubAnalyzeResponse(
        case=case,
        services=[ServiceInfo(**svc) for svc in services_with_paths],
        migration_context=migration_context,
        analysis=analysis,
    )


# ── GET /github/repos ─────────────────────────────────────────────────────────

@router.get(
    "/repos",
    status_code=status.HTTP_200_OK,
)
async def get_repos(
    github_token: str = Query(..., min_length=1, max_length=200),
    github_url: str = Query(..., min_length=1, max_length=500),
):
    """Lists available repos for the React interface.

    Returns only active repos (not archived, not forks).
    Called before POST /github/analyze so the client can select repos.
    """
    validate_github_token(github_token)
    account = detect_account_type(github_token, github_url)
    repos = list_repos(github_token, account)
    return {
        "account": account,
        "repos": repos,
        "total": len(repos),
    }


# ── stack_analyzer adapter ───────────────────────────────────────────────────

def _call_stack_analyzer(
    token: str,
    services: list[dict[str, Any]],
    migration_context: dict[str, Any],
) -> dict[str, Any]:
    """Adapts the entry-layer payload to the run_stack_analyzer interface.

    run_stack_analyzer operates on ONE repo at a time (via MigrationState).
    For multi-service (CASE B multi-repos), we iterate and merge dependency_graphs.

    The token is passed via the state dict (state["github_token"]) so it is
    scoped to this call and never leaks to other threads via os.environ.
    """
    try:
        from services.stack_analyzer import run_stack_analyzer, merge_graphs
    except ImportError as exc:
        logger.error(f"Cannot import stack_analyzer: {exc}")
        raise HTTPException(
            status_code=500,
            detail="stack_analyzer unavailable — check installation",
        )

    per_repo_graphs: list[dict] = []
    metadata: dict = {}
    all_priorities: list = []
    all_inventories: list = []
    errors: list[str] = []

    for svc in services:
        repo_full_name = svc["repo"]
        repo_url = f"https://github.com/{repo_full_name}"

        state: dict[str, Any] = {
            "repo_url": repo_url,
            "github_token": token,
            "source_cloud": migration_context.get("cloud_source", ""),
            "target_cloud": migration_context.get("cloud_target", ""),
            "monthly_budget_usd": migration_context.get("budget_max"),
            "target_region": migration_context.get("target_region", ""),
            "data_residency_requirement": migration_context.get("data_residency", ""),
            "regulatory_constraints": "",
            "ai_stack": {},
        }

        try:
            result = run_stack_analyzer(state)
        except Exception as exc:
            logger.error(f"stack_analyzer failed for {repo_url}: {exc}")
            errors.append(f"{svc['service_name']}: {exc}")
            continue

        dep_graph = result.get("dependency_graph") or {}
        nodes = dep_graph.get("nodes", [])
        edges = dep_graph.get("edges", [])

        for node in nodes:
            node.setdefault("service_name_override", svc["service_name"])

        # Collect per-repo graph in the format merge_graphs expects
        per_repo_graphs.append({
            "repo_origin": repo_full_name,
            "resources": {"nodes": nodes, "edges": edges},
        })

        metadata[svc["service_name"]] = {
            "repo": repo_full_name,
            "path": svc.get("path", "/"),
            "local_path": svc.get("local_path", ""),
        }

        all_priorities.extend(result.get("migration_priority", []))
        all_inventories.extend(result.get("source_services_inventory", []))

    # Deduplicate nodes across repos via canonical_id hash + infer cross-repo edges
    fused = merge_graphs(per_repo_graphs)
    fused_resources = fused.get("resources", {})

    return {
        "dependency_graph": {
            # Keep the nested "resources" shape expected by frontend and stack_analyzer consumers
            "resources": {
                "nodes": fused_resources.get("nodes", []),
                "edges": fused_resources.get("edges", []),
            },
            "cross_repo_edges": fused.get("cross_repo_edges", []),
            "conflicts": fused.get("conflicts", []),
            "metadata": metadata,
        },
        "migration_priority": all_priorities,
        "source_services_inventory": all_inventories,
        "errors": errors,
    }
