"""
stack_analyzer.py — Static Infrastructure & AI Stack Analyzer
================================================================
Analyse UNIQUEMENT les fichiers Terraform/Bicep/CloudFormation d'un repo GitHub.
+ Détecte et analyse les AI stacks (LLM, embeddings, vector stores) en Python.

Pipeline 100% déterministe (zéro LLM) :
1. Clone le repo via GitHub MCP
2. Liste les fichiers IaC (.tf, .bicep, .yaml CloudFormation)
3. Parse chaque fichier pour extraire les services cloud
4. Analyse les fichiers Python pour détecter les AI stacks en input
5. Parse les instanciations (AI classes) et variables constantes
6. Intègre l'AI stack fourni par l'utilisateur (state["ai_stack"])
7. Construit le dependency_graph avec scores et priorités

AUCUN code source Python n'est envoyé à un LLM.
Le cloud source est détecté depuis les préfixes Terraform
(aws_ → AWS, azurerm_ → Azure, google_ → GCP).
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import re
import time
import threading
from pathlib import Path
from typing import Any, Dict

import yaml

try:
    import hcl2 as _hcl2
except ImportError:
    _hcl2 = None  # type: ignore[assignment]

try:
    import networkx as nx
except ImportError:
    nx = None  # type: ignore[assignment]

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

from agents.state_validator import MigrationStateValidator
from services.stack_analyzer.github_tools import (
    _HAS_PYGITHUB,
    _pygithub_list_files,
    _pygithub_fetch_file,
    _pygithub_clone_info,
    _pygithub_list_infra_files,
)

# ── Focused sub-modules (split from this file) ────────────────────────────────
from services.stack_analyzer.constants import (
    _safe_list, _safe_dict, _safe_str,
    _AWS_SERVICES_MAP, _GCP_SERVICES_MAP, _AZURE_SERVICES_MAP,
    AI_PROVIDER_IMPORTS, AI_PROVIDER_PREFIXES, AI_VECTOR_IMPORTS,
    AI_CLASSES, EMBEDDING_DIMENSIONS,
    _SERVICE_TYPE_MAP, PULUMI_PYTHON_IMPORTS, _PULUMI_PROVIDER_MAP,
    _TYPE_COMPLEXITY_WEIGHT, _TYPE_PRIORITY,
    _CATEGORY_KEYWORD_RULES, _RELATION_PATTERNS, _INFRA_SDKS,
)
from services.stack_analyzer.ai_analyzer import AIPythonAnalyzer
from services.stack_analyzer.service_classifier import (
    _normalize_service_name, _classify_by_keywords,
    _get_service_type, _get_complexity,
)
from services.stack_analyzer.graph_builder import (
    _infer_relation, _build_dependency_edges, _build_nx_graph, _detect_cycles,
    _build_migration_priority, _build_services_inventory, _empty_graph,
    _is_infra_sdk, _apply_dampening,
)
from services.stack_analyzer.iac_parsers import (
    _detect_pulumi_iac,
    _parse_tf_hcl2, _extract_tf_cross_refs, _parse_tf_regex_fallback,
    _cfn_safe_loader, _parse_cf_yaml,
    _parse_arm_template, _parse_helm_chart, _detect_cdk_pulumi,
    _parse_tf_variables, _parse_tfvars,
)
from services.stack_analyzer.manifest_parser import (
    _parse_dependency_manifests, _parse_sdk_client_calls,
    _parse_docker_compose, _scan_env_var_patterns, _merge_layer2_results,
    _scan_requirements_for_ai, _scan_env_for_ai_keys,
)
from services.stack_analyzer.repo_orchestrator import (
    _detect_inter_repo_edges, _merge_dependency_graphs, _classify_infra_file,
)

from services.stack_analyzer.code_analysis_tools import compute_dependency_score

logger = logging.getLogger("StackAnalyzer")

_MCP_TIMEOUT_SECONDS = 30



# ─────────────────────────────────────────────────────────────────────────────
# File fetch (TTL cache — 24h)
# ─────────────────────────────────────────────────────────────────────────────

_file_cache: dict[str, tuple[str, float]] = {}
_file_cache_lock = threading.Lock()
_FILE_CACHE_TTL: int = int(os.getenv("IAC_CACHE_TTL", "86400"))


def _fetch_iac_file(owner: str, repo: str, fpath: str, branch: str, token: str) -> str:
    """Récupère le contenu d'un fichier IaC via PyGithub (direct, pas de MCP)."""
    token = token or os.environ.get("GITHUB_TOKEN", "")

    if _HAS_PYGITHUB:
        logger.debug(f"_fetch_iac_file: PyGithub for {fpath}")
        content = _pygithub_fetch_file(owner, repo, fpath, branch, token)
        if content:
            return content
        logger.debug(f"_fetch_iac_file: {fpath} not found or empty in {owner}/{repo}")
        return ""

    logger.warning(f"_fetch_iac_file: PyGithub unavailable, cannot fetch {fpath}")
    return ""


def _cached_fetch(owner: str, repo: str, fpath: str, branch: str, token: str) -> str:
    key = f"{owner}/{repo}/{branch}/{fpath}"
    now = time.monotonic()
    entry = _file_cache.get(key)
    if entry is not None:
        cached_content, ts = entry
        if now - ts < _FILE_CACHE_TTL:
            logger.debug(f"_cached_fetch: cache hit — {fpath}")
            return cached_content
    content = _fetch_iac_file(owner, repo, fpath, branch, token)
    with _file_cache_lock:
        _file_cache[key] = (content, now)
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Parse Python files for AI Stack (using PyGithub to fetch files)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_and_analyze_python_files(
    owner: str, repo: str, branch: str, github_token: str,
) -> dict:
    """Fetch and analyze Python files from GitHub for AI Stack detection.

    Uses PyGithub to fetch files in priority order:
    1. Root .py files (main.py, app.py, config.py, settings.py)
    2. ai/, llm/, agents/, rag/, src/ directory files
    3. Other .py files (limited to first 50)

    Returns dict with AI stack analysis results.
    """
    if not _HAS_PYGITHUB:
        logger.debug("_fetch_and_analyze_python_files: PyGithub not available")
        return {}

    analyzer = AIPythonAnalyzer()

    try:
        from github import Github
    except ImportError:
        logger.debug("_fetch_and_analyze_python_files: Cannot import Github")
        return {}

    try:
        g = Github(github_token) if github_token else Github()
        gh_repo = g.get_repo(f"{owner}/{repo}")  # works for both users and orgs

        # Collect Python files by priority
        root_files = []
        priority_files = []
        other_files = []

        # Fetch root priority files
        try:
            root_contents = gh_repo.get_contents("", ref=branch)
            for item in root_contents:
                if item.name.endswith(".py") and item.name in [
                    "main.py", "app.py", "config.py", "settings.py", "run.py"
                ]:
                    root_files.append((item.path, True))  # True = priority
        except Exception as e:
            logger.debug(f"_fetch_and_analyze_python_files: root scan failed — {e}")

        # Fetch priority directories
        priority_dirs = ["ai", "llm", "agents", "rag", "src"]
        for dir_name in priority_dirs:
            try:
                dir_contents = gh_repo.get_contents(dir_name, ref=branch)
                if not isinstance(dir_contents, list):
                    dir_contents = [dir_contents]

                for item in dir_contents:
                    if item.name.endswith(".py"):
                        priority_files.append((item.path, True))
            except Exception:
                pass  # Directory doesn't exist

        # Fetch all other Python files (limit)
        try:
            all_files = gh_repo.get_contents("", ref=branch)
            for item in all_files:
                if item.name.endswith(".py"):
                    if not any(root.startswith(item.path) for root, _ in root_files):
                        if not any(prio.startswith(item.path) for prio, _ in priority_files):
                            other_files.append((item.path, False))
        except Exception:
            pass

        # Process files in priority order
        file_list = root_files + priority_files + other_files[:30]
        analyzed_files = []

        for file_path, is_priority in file_list:
            try:
                file_obj = gh_repo.get_contents(file_path, ref=branch)

                # Skip large files
                if hasattr(file_obj, 'size') and file_obj.size > 50000:
                    logger.debug(f"_fetch_and_analyze_python_files: {file_path} too large, skipping")
                    continue

                source_code = file_obj.decoded_content.decode("utf-8", errors="ignore")

                # Analyze this file
                file_imports = analyzer.scan_imports(source_code)
                file_constants = analyzer.resolve_constants(source_code)
                file_insts = analyzer.scan_instantiations(source_code, file_path, constants=file_constants)

                # Add to results if found AI stack related code
                if (file_imports.get("llm_providers") or
                    file_imports.get("vector_stores") or
                    file_insts):
                    analyzed_files.append({
                        "file": file_path,
                        "imports": file_imports,
                        "instantiations": file_insts,
                        "constants": file_constants,
                    })
                    logger.debug(f"_fetch_and_analyze_python_files: {file_path} analyzed (AI stack found)")

            except Exception as e:
                logger.debug(f"_fetch_and_analyze_python_files: {file_path} — {e}")
                continue

        # Aggregate results
        llm_detected = {}
        embeddings_detected = {}
        vector_stores_detected = []
        portable_components = []
        providers = set()

        for file_result in analyzed_files:
            for inst in file_result["instantiations"]:
                inst_type = inst.get("type", "")
                inst_class = inst.get("class", "")

                if inst_type == "llm":
                    model_id = inst.get("kwargs", {}).get("model_id") or inst.get("kwargs", {}).get("model", "")
                    llm_detected[inst_class] = {
                        "model_id": model_id,
                        "file": inst.get("file"),
                        "line": inst.get("line"),
                    }
                    providers.add(inst_class)

                elif inst_type == "embeddings":
                    model_id = inst.get("kwargs", {}).get("model_id") or inst.get("kwargs", {}).get("model", "")
                    dims = EMBEDDING_DIMENSIONS.get(model_id)
                    embeddings_detected[inst_class] = {
                        "model_id": model_id,
                        "dimension": dims,
                        "reembedding_required": dims is not None,
                        "file": inst.get("file"),
                        "line": inst.get("line"),
                    }
                    providers.add(inst_class)

                elif inst_type == "vector_store":
                    vector_stores_detected.append({
                        "class": inst_class,
                        "file": inst.get("file"),
                        "line": inst.get("line"),
                        "portable": inst_class in ["FAISS", "Chroma", "Weaviate", "Qdrant"],
                    })
                    if inst_class in ["FAISS", "Chroma", "Weaviate", "Qdrant"]:
                        portable_components.append(inst_class)
                    providers.add(inst_class)

        return {
            "files_analyzed": len(analyzed_files),
            "providers_detected": list(providers),
            "llm": llm_detected,
            "embeddings": embeddings_detected,
            "vector_stores": vector_stores_detected,
            "portable_components": list(set(portable_components)),
        }

    except Exception as e:
        logger.warning(f"_fetch_and_analyze_python_files: {type(e).__name__}: {e}")
        return {}






def _parse_iac_files(
    owner: str, repo: str, branch: str, github_token: str,
    infra: dict,
) -> tuple[list[dict], list[dict], int]:
    """Parse les fichiers IaC pour extraire les ressources cloud déclarées.

    Retourne (iac_resources, iac_cross_refs, files_skipped).

    Priorité : python-hcl2 → regex fallback (Terraform)
               regex only (Bicep)
               pyyaml → regex (CloudFormation)
    """
    iac_resources: list[dict] = []
    iac_cross_refs: list[dict] = []

    tf_files = infra.get("terraform", [])
    bicep_files = [f for f in infra.get("other", infra.get("bicep", []))
                   if str(f).endswith(".bicep")]
    cf_files = [f for f in infra.get("cloudformation", infra.get("yaml", []))
                if str(f).endswith((".yaml", ".yml"))]

    all_iac = ([(f, "tf") for f in tf_files]
               + [(f, "bicep") for f in bicep_files]
               + [(f, "cf") for f in cf_files])

    if not all_iac:
        return [], [], 0

    # ── Build Terraform variable map from variables.tf + *.tfvars ────────────
    # This allows _parse_tf_hcl2 to resolve ${var.name} references in
    # instance_class, assume_role_policy, etc. instead of leaving them unresolved.
    tf_var_values: dict[str, str] = {}
    _var_files = (
        [(p, "tf")     for p in tf_files       if p.endswith("variables.tf")]
        + [(p, "tfvars") for p in infra.get("tfvars", [])]
    )
    for _vpath, _vtype in _var_files:
        try:
            _vcontent = _cached_fetch(owner, repo, _vpath, branch, github_token)
            if not _vcontent:
                continue
            if _vtype == "tf":
                tf_var_values.update(_parse_tf_variables(_vcontent))
            else:
                tf_var_values.update(_parse_tfvars(_vcontent))
        except Exception as _ve:
            logger.debug("tf var parse failed for %s: %s", _vpath, _ve)
    if tf_var_values:
        logger.info("_parse_iac_files: resolved %d Terraform variables", len(tf_var_values))

    MAX_FILES = 20
    files_skipped = max(0, len(all_iac) - MAX_FILES)
    if files_skipped > 0:
        logger.warning(
            f"_parse_iac_files: {files_skipped} fichiers ignorés (cap={MAX_FILES}). "
            f"Augmenter la limite via IAC_MAX_FILES env var."
        )

    for fpath, ftype in all_iac[:MAX_FILES]:
        try:
            content = _cached_fetch(owner, repo, fpath, branch, github_token)
            if not content:
                continue

            if ftype == "tf":
                # Priority chain: hcl2 → regex
                if _hcl2 is not None:
                    try:
                        resources, cross_refs = _parse_tf_hcl2(content, fpath, tf_var_values)
                        iac_resources.extend(resources)
                        iac_cross_refs.extend(cross_refs)
                    except Exception as hcl_exc:
                        logger.debug(f"hcl2 échec pour {fpath}: {hcl_exc} — regex fallback")
                        iac_resources.extend(_parse_tf_regex_fallback(content, fpath))
                else:
                    iac_resources.extend(_parse_tf_regex_fallback(content, fpath))

            elif ftype == "bicep":
                # Direct regex parsing avec mapping Azure enrichi
                for match in re.finditer(r"resource\s+\w+\s+'(Microsoft\.[^/]+)/", content, re.IGNORECASE):
                    ns = match.group(1).lower()
                    svc_type, cloud = _BICEP_AZURE_MAP.get(ns, ("unknown", "azure"))
                    svc_name = _normalize_service_name(ns.replace("microsoft.", "azurerm_"))
                    iac_resources.append({
                        "service": svc_name,
                        "cloud": cloud,
                        "file": fpath,
                        "source": "bicep_regex",
                    })

            elif ftype == "cf":
                cf_resources = _parse_cf_yaml(content, fpath)
                if cf_resources:
                    iac_resources.extend(cf_resources)
                else:
                    for match in re.finditer(r'Type:\s*(AWS|Azure|Google)::(\w+)::', content):
                        cloud = {"AWS": "aws", "Azure": "azure", "Google": "gcp"}[match.group(1)]
                        iac_resources.append({
                            "service": match.group(2).lower(),
                            "cloud": cloud, "file": fpath,
                        })

        except Exception as exc:
            logger.debug(f"_parse_iac_files: échec pour {fpath}: {exc}")
            continue

    logger.info(f"_parse_iac_files: {len(iac_resources)} ressources IaC détectées")
    return iac_resources, iac_cross_refs, files_skipped


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation — clone + list infra files
# ─────────────────────────────────────────────────────────────────────────────

def _init_batch(github_token: str, repo_url: str) -> tuple[dict, dict]:
    """Récupère les infos du repo et liste les fichiers IaC via PyGithub (rapide, pas de MCP).

    Retourne (clone_result, infra_result).
    Ne liste PAS les fichiers Python — uniquement les fichiers IaC.
    """
    # 1. Get repo info (owner, repo, branch) via PyGithub
    clone = _pygithub_clone_info(repo_url, github_token)
    owner  = clone.get("owner", "")
    repo   = clone.get("repo", "")
    branch = clone.get("branch", "main")

    if not owner or not repo or clone.get("status") == "error":
        logger.warning(f"git_clone échoué: {clone.get('detail', 'inconnu')}")
        return clone, {}

    # 2. List infrastructure files via PyGithub (no MCP!)
    infra_result = _pygithub_list_infra_files(owner, repo, branch, github_token)

    infra_tf = len(infra_result.get("terraform", []))
    infra_docker = len(infra_result.get("docker_compose", []))
    infra_dockerfile = len(infra_result.get("dockerfile", []))
    logger.info(
        f"_init_batch: {infra_tf} Terraform + {infra_docker} docker-compose + {infra_dockerfile} Dockerfile "
        f"dans {owner}/{repo}@{branch}"
    )
    return clone, infra_result


# ─────────────────────────────────────────────────────────────────────────────
# IaC-specific scoring
# ─────────────────────────────────────────────────────────────────────────────

def _score_iac_resources(iac_resources: list[dict]) -> dict[str, int]:
    """Calcule le score de chaque service IaC par comptage d'occurrences.

    Score = 2 (première occurrence) + 1 par occurrence supplémentaire.
    Équivalent à SERVICE_WEIGHT + fréquence dans compute_dependency_score.
    """
    counts: dict[str, int] = {}
    for res in iac_resources:
        svc = res.get("service", "")
        if svc:
            counts[svc] = counts.get(svc, 0) + 1

    scores: dict[str, int] = {}
    for svc, count in counts.items():
        svc_type = _get_service_type(svc)
        weight = _TYPE_COMPLEXITY_WEIGHT.get(svc_type, 1.0)
        raw = max(2, count * 2)
        scores[svc] = max(2, round(raw * weight))
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Finalize dependency graph (IaC-only, no AST)
# ─────────────────────────────────────────────────────────────────────────────

def _finalize_iac_graph(
    graph: dict,
    iac_resources: list[dict],
    iac_cross_refs: list[dict],
    files_analyzed: int,
    source_cloud: str,
    ai_stack: dict,
    ai_stack_analysis: dict | None = None,
    source_files: dict | None = None,
) -> None:
    """Construit le dependency_graph depuis les ressources IaC (zéro AST).

    Mutates graph in place.
    """
    # ── Score IaC resources ───────────────────────────────────────────────────
    resource_scores = _score_iac_resources(iac_resources)

    # ── Apply dampening ───────────────────────────────────────────────────────
    effective_cloud = source_cloud or "unknown"
    for svc in list(resource_scores):
        dampened = _apply_dampening(svc, resource_scores[svc], effective_cloud)
        if dampened != resource_scores[svc]:
            logger.debug(f"dampening {svc} ({effective_cloud}): {resource_scores[svc]} → {dampened}")
            resource_scores[svc] = dampened

    # ── Resolve detected cloud ────────────────────────────────────────────────
    if source_cloud:
        graph["detected_cloud"] = source_cloud
        graph["cloud_detection_method"] = "user_provided"
    else:
        # Auto-detect from resource prefixes
        cloud_counts: dict[str, int] = {}
        for res in iac_resources:
            c = res.get("cloud", "unknown")
            if c != "unknown":
                cloud_counts[c] = cloud_counts.get(c, 0) + 1
        if cloud_counts:
            if len(cloud_counts) == 1:
                detected = list(cloud_counts.keys())[0]
                graph["detected_cloud"] = detected
                graph["cloud_detection_method"] = "iac_prefix"
                logger.info(f"_finalize_iac_graph: cloud auto-détecté = {detected}")
            else:
                # Multiple cloud providers detected in the same infrastructure
                graph["detected_cloud"] = "multi"
                graph["cloud_detection_method"] = "iac_prefix_multi"
                graph["cloud_distribution"] = dict(cloud_counts)
                logger.info(
                    f"_finalize_iac_graph: source multi-cloud détecté = {cloud_counts}"
                )

    detected_cloud = graph.get("detected_cloud", "unknown")

    # ── Build unique services list (deduplicated) ─────────────────────────────
    seen: set[str] = set()
    unique_resources: list[dict] = []
    for res in iac_resources:
        svc = res.get("service", "")
        if svc and svc not in seen:
            seen.add(svc)
            unique_resources.append(res)

    # ── Build nodes ───────────────────────────────────────────────────────────
    # Merge contextual_hints from all occurrences of the same resource type
    # (e.g. engine="postgres" from aws_db_instance) so Agent 01 can make
    # engine-aware target mappings (PostgreSQL vs MySQL vs SQL Server).
    hints_by_svc: dict[str, dict] = {}
    for res in unique_resources:
        svc = res["service"]
        hints = res.get("contextual_hints")
        if hints and isinstance(hints, dict):
            hints_by_svc.setdefault(svc, {}).update(hints)

    nodes = []
    resource_set = set()
    for res in unique_resources:
        svc = res["service"]
        score = resource_scores.get(svc, 2)
        cloud = res.get("cloud", detected_cloud)
        if cloud == "unknown":
            cloud = detected_cloud
        node: dict = {
            "id": svc,
            "type": _get_service_type(svc),
            "cloud": cloud,
            "score": score,
            "complexity": _get_complexity(score),
            "source": "iac",
            "cloud_confirmed": True,
        }
        if svc in hints_by_svc:
            node["contextual_hints"] = hints_by_svc[svc]
        nodes.append(node)
        resource_set.add(svc)

    # ── Build edges from IaC cross-references ─────────────────────────────────
    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for ref in iac_cross_refs:
        edge_key = (ref["from"], ref["to"])
        if edge_key not in seen_edges and ref["from"] in resource_set and ref["to"] in resource_set:
            seen_edges.add(edge_key)
            edges.append({
                "from": ref["from"], "to": ref["to"],
                "relation": ref.get("relation", "references"),
                "weight": 1,
            })

    graph["resources"] = {"nodes": nodes, "edges": edges}
    graph["resource_scores"] = resource_scores

    # ── NetworkX cycle detection ──────────────────────────────────────────────
    if nx is not None:
        G = _build_nx_graph(nodes, edges)
        if G is not None:
            cycles = _detect_cycles(G)
            graph["resources"]["has_cycles"] = bool(cycles)
            graph["resources"]["cycles"] = cycles

    graph["files_analyzed"] = files_analyzed
    graph["unused_resources"] = []

    # ── Inject AI stack from user input + detected Python analysis ──────────
    graph["framework"] = ai_stack.get("framework") or None
    graph["llm_provider"] = ai_stack.get("llm_provider") or None
    graph["vector_db"] = ai_stack.get("vector_db") or None
    graph["embedding_model"] = ai_stack.get("embedding_model") or None

    # ── Build ai_stack summary ────────────────────────────────────────────────
    # Priority order (source of truth first):
    #   1. IaC (Terraform/Bicep) — what is actually provisioned
    #   2. Python code / requirements.txt / .env — how it is used
    #   3. User input — manual override/fallback
    # IaC goes first because it defines what exists in infrastructure.
    # Python enriches (model_id, framework, vector store) and covers cases
    # where an LLM is called via HTTP without a dedicated IaC resource.

    # ── AI Stack detection — strict cascade (each step fills only gaps) ──────
    #
    #  1. IaC  (Terraform/Bicep)   — source de vérité infra
    #  2. Python AST               — imports, instanciations, boto3.client()
    #  3. requirements.txt         — packages IA installés
    #  4. .env / .env.example      — clés API présentes
    #  5. HTTP direct              — requests.post vers api.openai.com, etc.
    #  6. User input               — saisie manuelle dans le formulaire
    #
    # Règle : chaque étape ne remplace QUE les champs encore None/vides.
    # IaC fixe llm_provider → Python peut enrichir model_id/embeddings/framework
    # mais NE remplace PAS llm_provider déjà fixé.

    detected_llm       : str | None  = None
    detected_model_id  : str | None  = None
    detected_embeddings: dict        = {}
    detected_vector_db : list        = []
    detected_framework : str | None  = None
    detected_providers : list        = []
    detection_method   : str         = "user_input"

    # ── 1. IaC nodes ─────────────────────────────────────────────────────────
    _IaC_AI_SERVICES = {
        "bedrock":          "aws_bedrock",
        "bedrock-runtime":  "aws_bedrock",
        "sagemaker":        "aws_sagemaker",
        "cognitive":        "azure_openai",
        "openai":           "azure_openai",
        "vertex-ai":        "gcp_vertex",
        "aiplatform":       "gcp_vertex",
    }
    for node in nodes:
        hints  = node.get("contextual_hints") or {}
        ai_svc = hints.get("ai_service", "")
        if ai_svc and ai_svc in _IaC_AI_SERVICES:
            detected_llm      = _IaC_AI_SERVICES[ai_svc]
            model_id          = hints.get("model_id", "")
            detected_model_id = model_id if (model_id and model_id != "unknown") else None
            detected_providers = [detected_llm]
            detection_method   = "iac_terraform"
            break

    # ── 2. Python AST ─────────────────────────────────────────────────────────
    if isinstance(ai_stack_analysis, dict) and ai_stack_analysis.get("providers_detected"):
        py_providers = ai_stack_analysis.get("providers_detected", [])
        llm_dict     = ai_stack_analysis.get("llm", {})
        py_llm       = list(llm_dict.keys())[0]   if llm_dict else None
        py_model_id  = list(llm_dict.values())[0].get("model_id") if llm_dict else None
        py_embeddings = ai_stack_analysis.get("embeddings", {})
        py_vector_db  = ai_stack_analysis.get("vector_stores", [])

        if not detected_llm:
            detected_llm       = py_llm
            detected_providers = py_providers
            detection_method   = "python_ast"
        if not detected_model_id and py_model_id:
            detected_model_id = py_model_id
        if not detected_embeddings and py_embeddings:
            detected_embeddings = py_embeddings
        if not detected_vector_db and py_vector_db:
            detected_vector_db = py_vector_db

    # ── 3 & 4. requirements.txt + .env ───────────────────────────────────────
    if source_files:
        for fname, content in source_files.items():
            bname = fname.split("/")[-1].lower()
            if bname in ("requirements.txt", "requirements-dev.txt",
                         "requirements_dev.txt", "pyproject.toml", "setup.cfg"):
                req_ai = _scan_requirements_for_ai(content)
                if not detected_llm and req_ai.get("llm_provider"):
                    detected_llm       = req_ai["llm_provider"]
                    detected_providers = [detected_llm]
                    detection_method   = "requirements_txt"
                if not detected_embeddings and req_ai.get("embedding_model"):
                    detected_embeddings = {req_ai["embedding_model"]: {}}
                if not detected_vector_db and req_ai.get("vector_db"):
                    detected_vector_db = [{"class": req_ai["vector_db"]}]
                if not detected_framework and req_ai.get("framework"):
                    detected_framework = req_ai["framework"]

            elif bname in (".env", ".env.example", ".env.template", ".env.sample"):
                env_provider = _scan_env_for_ai_keys(content)
                if env_provider and not detected_llm:
                    detected_llm       = env_provider
                    detected_providers = [detected_llm]
                    detection_method   = "env_file"

    # ── 5. HTTP direct — requests.post / httpx.post vers endpoints LLM connus ─
    # Détecte les appels API LLM sans SDK installé (ex: requests.post("https://api.openai.com/..."))
    if not detected_llm and source_files:
        _HTTP_LLM_ENDPOINTS: list[tuple[str, str]] = [
            ("api.openai.com",              "openai"),
            ("api.anthropic.com",           "anthropic_claude"),
            ("api.mistral.ai",              "mistral_ai"),
            ("api.cohere.com",              "cohere_ai"),
            ("api.groq.com",                "groq_ai"),
            ("api.together.xyz",            "together_ai"),
            ("api.replicate.com",           "replicate_ai"),
            ("api.fireworks.ai",            "fireworks_ai"),
            ("generativelanguage.googleapis.com", "gcp_gemini"),
            ("aiplatform.googleapis.com",   "gcp_vertex"),
            ("openai.azure.com",            "azure_openai"),
            ("cognitiveservices.azure.com", "azure_openai"),
            ("bedrock-runtime.amazonaws.com", "aws_bedrock"),
            ("api.ai21.com",                "ai21"),
            ("api.perplexity.ai",           "perplexity_ai"),
        ]
        for fname, content in source_files.items():
            if not fname.endswith(".py"):
                continue
            for endpoint, provider in _HTTP_LLM_ENDPOINTS:
                if endpoint in content:
                    detected_llm       = provider
                    detected_providers = [provider]
                    detection_method   = "http_direct"
                    break
            if detected_llm:
                break

    # ── 6. User input — override final ───────────────────────────────────────
    final_llm       = ai_stack.get("llm_provider")    or detected_llm
    final_vector_db = ai_stack.get("vector_db")       or (detected_vector_db[0].get("class") if detected_vector_db else None)
    final_embedding = ai_stack.get("embedding_model") or (list(detected_embeddings.keys())[0] if detected_embeddings else None)
    final_framework = ai_stack.get("framework")       or detected_framework

    if ai_stack.get("llm_provider"):
        detection_method = "user_input"

    # ── Build iac_ai_resources — list consumed by AIStackSection frontend ─────
    # One entry per IaC node that carries an ai_service hint (bedrock, sagemaker…)
    _IaC_TYPE_MAP = {
        "bedrock":         "managed_llm",
        "bedrock-runtime": "managed_llm",
        "sagemaker":       "managed_ml",
        "cognitive":       "managed_llm",
        "openai":          "managed_llm",
        "vertex-ai":       "managed_ml",
        "aiplatform":      "managed_ml",
    }
    iac_ai_resources = []
    for node in nodes:
        hints = node.get("contextual_hints") or {}
        ai_svc = hints.get("ai_service", "")
        if ai_svc and ai_svc in _IaC_TYPE_MAP:
            model_id = hints.get("model_id", "")
            label = f"{ai_svc}" + (f" ({model_id})" if model_id and model_id != "unknown" else "")
            iac_ai_resources.append({
                "name": label,
                "type": _IaC_TYPE_MAP[ai_svc],
                "resource_type": node.get("resource_type", ai_svc),
                "model_id": model_id,
            })

    graph["ai_stack"] = {
        "llm_provider": final_llm,
        "llm_model_ids": [detected_model_id] if detected_model_id else [],
        "embedding_model": final_embedding,
        "embedding_dims": None,
        "embedding_type": None,
        "embedding_portable": None,
        "vector_db": final_vector_db,
        "framework": final_framework,
        "agent_frameworks": [final_framework] if final_framework else [],
        "mcp_detected": False,
        "mcp_servers": [],
        "a2a_detected": False,
        "detected_llm": detected_llm,
        "detected_embeddings": detected_embeddings,
        "detected_vector_stores": detected_vector_db,
        "detected_providers": detected_providers,
        "detection_method": detection_method,
        "iac_ai_resources": iac_ai_resources,
    }

    logger.info(
        f"_finalize_iac_graph: {len(nodes)} ressources, "
        f"cloud={detected_cloud}, "
        f"ai_stack={ai_stack}, "
        f"{files_analyzed} fichiers IaC analysés"
    )


def _analyze_single_repo(
    repo_url: str,
    github_token: str,
    source_cloud: str,
    ai_stack: dict,
) -> tuple[dict, dict[str, str]]:
    """Analyze one GitHub repo and return (dependency_graph, source_files).

    Detection order:
      1. Terraform / Bicep / CloudFormation / ARM / Helm  (HIGH confidence)
      2. No IaC found → manual service selection required
         Python files / requirements.txt / .env are collected but reserved
         for Agent 02 (code migration), not for cloud source detection.
    """
    from services.stack_analyzer.github_tools import (
        _pygithub_clone_info,
        _pygithub_list_infra_files,
    )

    graph = _empty_graph()
    source_files: dict[str, str] = {}

    clone = _pygithub_clone_info(repo_url, github_token)
    owner = clone.get("owner", "")
    repo  = clone.get("repo", "")
    branch = clone.get("branch", "main")

    if not owner or not repo or clone.get("status") == "error":
        graph["error"] = f"Could not resolve repository: {clone.get('detail', 'unknown')}"
        return graph, source_files

    infra = _pygithub_list_infra_files(owner, repo, branch, github_token)

    # ── Count IaC files (Terraform, Bicep, CloudFormation, ARM JSON, Helm) ────
    iac_keys = ["terraform", "bicep", "cloudformation", "yaml", "arm", "helm"]

    # Hard cap on files per category to prevent runaway monorepos from exhausting
    # GitHub API quota. Override via IAC_MAX_FILES env var (default: 200).
    _max_iac = int(os.getenv("IAC_MAX_FILES", "200"))
    for _k in iac_keys:
        if len(infra.get(_k, [])) > _max_iac:
            logger.warning(
                f"_analyze_single_repo [{owner}/{repo}]: {_k} files capped at "
                f"{_max_iac} (was {len(infra[_k])}) — set IAC_MAX_FILES to raise"
            )
            infra[_k] = infra[_k][:_max_iac]

    total_infra = sum(len(infra.get(k, [])) for k in iac_keys)
    logger.info(f"_analyze_single_repo [{owner}/{repo}]: {total_infra} IaC files")

    # ── Collect Python source files (always — needed for AI stack + CDK detect) ──
    ai_stack_analysis, source_files = _fetch_and_analyze_python_files_with_content(
        owner, repo, branch, github_token
    )

    # ── CDK / Pulumi detection ────────────────────────────────────────────────
    manifest_packages = [r.get("service", "") for r in _parse_dependency_manifests(owner, repo, branch, github_token)]
    cdk_info = _detect_cdk_pulumi(manifest_packages, source_files)
    if cdk_info["cdk_detected"]:
        graph["cdk_detected"] = True
        graph["cdk_type"] = cdk_info["cdk_type"]
        graph.setdefault("warnings", [])
        graph["warnings"].append(cdk_info["cdk_warning"])

    # ── LEVEL 1: Parse IaC files ──────────────────────────────────────────────
    if total_infra > 0:
        graph["detection_mode"] = "iac"
        iac_resources: list[dict] = []
        iac_cross_refs: list[dict] = []

        # Standard IaC (TF, Bicep, CFN)
        standard_resources, standard_refs, files_skipped = _parse_iac_files(
            owner, repo, branch, github_token, infra
        )
        iac_resources.extend(standard_resources)
        iac_cross_refs.extend(standard_refs)

        # ARM JSON templates
        for fpath in infra.get("arm", []):
            content = _cached_fetch(owner, repo, fpath, branch, github_token)
            if content:
                iac_resources.extend(_parse_arm_template(content, fpath))

        # Helm Charts
        for chart_dir in infra.get("helm", []):
            values_content = _cached_fetch(owner, repo, f"{chart_dir}/values.yaml", branch, github_token)
            chart_content  = _cached_fetch(owner, repo, f"{chart_dir}/Chart.yaml",  branch, github_token)
            if values_content or chart_content:
                iac_resources.extend(_parse_helm_chart(
                    values_content or "", chart_content or "", chart_dir
                ))

        _finalize_iac_graph(
            graph=graph,
            iac_resources=iac_resources,
            iac_cross_refs=iac_cross_refs,
            files_analyzed=total_infra,
            source_cloud=source_cloud,
            ai_stack=ai_stack,
            ai_stack_analysis=ai_stack_analysis,
            source_files=source_files,
        )
        if ai_stack_analysis:
            graph["python_ai_stack"] = ai_stack_analysis
        return graph, source_files

    # ── LEVEL 2: No IaC — manual selection required ───────────────────────────
    # Python files / requirements.txt / .env are available in source_files for
    # Agent 02 (code migration phase) but are not used for cloud source detection.
    logger.warning(f"_analyze_single_repo [{owner}/{repo}]: no IaC files — manual selection required")
    graph["detection_mode"] = "user_selection"
    graph["needs_service_selection"] = True
    graph["detected_cloud"] = source_cloud or "unknown"
    if ai_stack_analysis:
        graph["python_ai_stack"] = ai_stack_analysis
    return graph, source_files


def run_stack_analyzer(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node — Static Infrastructure & AI Stack Analyzer.

    Pipeline 100% déterministe (zéro LLM) :
    1. Clone le repo via GitHub MCP
    2. Liste les fichiers IaC (.tf, .bicep, .yaml CloudFormation)
    3. Parse chaque fichier pour extraire les services cloud
    4. Analyse les fichiers Python pour détecter l'AI stack
    5. Intègre l'AI stack fourni par l'utilisateur (state["ai_stack"])
    6. Construit le dependency_graph avec scores et priorités

    Aucun code source n'est envoyé à un LLM.
    Le cloud source est détecté depuis les préfixes Terraform
    (aws_ → AWS, azurerm_ → Azure, google_ → GCP) ou depuis state["source_cloud"].
    """
    # ── Validation état ───────────────────────────────────────────────────────
    validation_errors = MigrationStateValidator.get_validation_errors(state, "analyze")
    if validation_errors:
        logger.error(f"StackAnalyzer: state validation failed: {validation_errors}")
        err_graph = _empty_graph("State validation failed")
        return {
            "dependency_graph": err_graph,
            "source_services_inventory": _build_services_inventory(err_graph),
            "migration_priority": _build_migration_priority(err_graph),
        }

    # ── Resolve repo list (multi-repo or single) ──────────────────────────────
    repo_urls: list[str] = state.get("repo_urls") or []
    if not repo_urls:
        single = state.get("repo_url", "")
        repo_urls = [single] if single else []

    source_cloud = state.get("source_cloud", "")
    ai_stack     = state.get("ai_stack") or {}

    # ── GitHub token ──────────────────────────────────────────────────────────
    raw_token = state.get("github_token") or os.getenv("GITHUB_TOKEN", "")
    github_token = raw_token.strip().strip('"').strip("'").strip("\r\n") if raw_token else ""

    if github_token:
        logger.info(f"StackAnalyzer: token disponible ({github_token[:4]}****)")
    else:
        logger.warning("StackAnalyzer: GITHUB_TOKEN absent")

    if not repo_urls:
        err_graph = _empty_graph("No repository URL provided")
        return {
            "dependency_graph": err_graph,
            "source_services_inventory": [],
            "migration_priority": [],
            "source_files": {},
            "errors": ["No repository URL provided"],
            "needs_service_selection": False,
        }

    _t_start = time.perf_counter()
    logger.info(
        f"StackAnalyzer: démarrage — {len(repo_urls)} repo(s), "
        f"source_cloud={source_cloud or 'auto'}"
    )

    # ── Analyze each repo individually ────────────────────────────────────────
    per_repo_graphs: list[dict] = []
    all_source_files: dict[str, dict[str, str]] = {}   # {repo_label: source_files}
    merged_source_files: dict[str, str] = {}
    any_needs_manual = False

    for repo_url in repo_urls:
        # Derive a short label for this repo (e.g. "owner/repo")
        repo_label = "/".join(repo_url.rstrip("/").split("/")[-2:]) if "/" in repo_url else repo_url
        logger.info(f"StackAnalyzer: analyzing repo {repo_label}")

        try:
            g, src_files = _analyze_single_repo(
                repo_url=repo_url,
                github_token=github_token,
                source_cloud=source_cloud,
                ai_stack=ai_stack,
            )
            g["_repo_label"] = repo_label
            per_repo_graphs.append(g)
            all_source_files[repo_label] = src_files
            merged_source_files.update(src_files)

            if g.get("needs_service_selection"):
                any_needs_manual = True
        except Exception as exc:
            logger.error(f"StackAnalyzer: error on repo {repo_label}: {exc}")
            err_g = _empty_graph(str(exc))
            err_g["_repo_label"] = repo_label
            per_repo_graphs.append(err_g)

    # ── If all repos need manual selection, surface that immediately ───────────
    if any_needs_manual and not any(
        bool(g.get("resources", {}).get("nodes"))
        for g in per_repo_graphs
    ):
        err_graph = _empty_graph()
        err_graph["detection_mode"] = "user_selection"
        err_graph["needs_service_selection"] = True
        err_graph["detected_cloud"] = source_cloud or "unknown"
        return {
            "dependency_graph": err_graph,
            "source_services_inventory": [],
            "migration_priority": [],
            "source_files": {},
            "errors": [],
            "needs_service_selection": True,
        }

    # ── Detect inter-repo edges ───────────────────────────────────────────────
    inter_edges: list[dict] = []
    if len(per_repo_graphs) > 1:
        inter_edges = _detect_inter_repo_edges(per_repo_graphs, all_source_files)
        if inter_edges:
            logger.info(f"StackAnalyzer: {len(inter_edges)} inter-repo edges detected")

    # ── Merge all repo graphs (Option C — virtual monorepo) ───────────────────
    graph = _merge_dependency_graphs(per_repo_graphs, inter_edges)

    # ── Validation ────────────────────────────────────────────────────────────
    errors: list[str] = []
    nodes = graph.get("resources", {}).get("nodes", [])
    if not nodes:
        msg = (
            "No cloud services detected across all repositories. "
            "Ensure at least one repo contains IaC (Terraform, Bicep, ARM, Helm) "
            "or a requirements.txt / docker-compose.yml with recognizable services."
        )
        errors.append(msg)
        logger.error(f"StackAnalyzer validation: {msg}")

    detected_cloud = graph.get("detected_cloud", "unknown")
    if detected_cloud == "unknown":
        errors.append(
            "Unable to determine source cloud provider. "
            "Provide source_cloud explicitly or ensure repos use recognized IaC prefixes."
        )

    needs_manual = graph.get("needs_service_selection", False)

    _elapsed = time.perf_counter() - _t_start
    logger.info(
        f"StackAnalyzer: done — {len(nodes)} services total, "
        f"{len(per_repo_graphs)} repo(s), cloud={detected_cloud}, "
        f"{len(inter_edges)} inter-repo edges — durée={_elapsed:.2f}s"
    )

    return {
        "dependency_graph": graph,
        "source_services_inventory": _build_services_inventory(graph),
        "migration_priority": _build_migration_priority(graph),
        "source_files": merged_source_files,
        "errors": errors,
        "needs_service_selection": needs_manual,
    }


def _fetch_and_analyze_python_files_with_content(
    owner: str, repo: str, branch: str, github_token: str,
) -> tuple[dict, dict[str, str]]:
    """Fetch Python file content using Git Tree API (1 call) + parallel downloads.

    Returns:
        (ai_stack_analysis, source_files)
        source_files: {filename: raw_python_source}
    """
    source_files: dict[str, str] = {}

    if not _HAS_PYGITHUB:
        return _fetch_and_analyze_python_files(owner, repo, branch, github_token), {}

    try:
        from github import Github
        from concurrent.futures import ThreadPoolExecutor, as_completed

        g = Github(github_token) if github_token else Github()
        gh_repo = g.get_repo(f"{owner}/{repo}")

        # 1 API call to get the full file tree (recursive=True)
        tree = gh_repo.get_git_tree(branch, recursive=True)
        py_blobs = [
            item for item in tree.tree
            if item.type == "blob"
            and item.path.endswith(".py")
            and (item.size or 0) <= 100_000
            and not any(seg in item.path.split("/") for seg in _SKIP_DIRS)
        ][:100]  # cap at 100 files

        # Also collect non-Python copyable files (requirements.txt, .env.example,
        # Dockerfiles, configs, ...) so agent_02 can carry over everything from the
        # source repo — migrating what needs migration, copying the rest as-is.
        # Reuses the tree we already fetched above (no extra API calls).
        def _is_copyable(item) -> bool:
            name_lower = item.path.rsplit("/", 1)[-1].lower()
            if name_lower in _SKIP_FILENAMES:
                return False
            ext = "." + name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
            is_dockerfile = name_lower in (
                "dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore",
            )
            return ext in _COPYABLE_EXTENSIONS or is_dockerfile

        extra_blobs = [
            item for item in tree.tree
            if item.type == "blob"
            and not item.path.endswith(".py")
            and (item.size or 0) <= 100_000
            and not any(seg in item.path.split("/") for seg in _SKIP_DIRS)
            and _is_copyable(item)
        ][:100]  # cap at 100 extra files

        all_blobs = py_blobs + extra_blobs

        def _fetch_one(item) -> tuple[str, str] | None:
            try:
                obj = gh_repo.get_git_blob(item.sha)
                import base64
                raw = base64.b64decode(obj.content).decode("utf-8", errors="ignore")
                return item.path, raw
            except Exception:
                return None

        # Parallel download — 8 threads, well within GitHub's rate limits
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, item): item for item in all_blobs}
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    source_files[result[0]] = result[1]

        logger.info(
            f"_fetch_and_analyze_python_files_with_content: "
            f"collected {len(py_blobs)} Python + {len(extra_blobs)} other copyable "
            f"files as source_files"
        )

    except Exception as exc:
        logger.warning(f"_fetch_and_analyze_python_files_with_content: {exc}")

    ai_analysis = _fetch_and_analyze_python_files(owner, repo, branch, github_token)
    return ai_analysis, source_files


_COPYABLE_EXTENSIONS = {
    ".py", ".ipynb",
    ".txt", ".md", ".rst", ".cfg", ".ini", ".toml", ".yaml", ".yml", ".json",
    ".sh", ".bash", ".dockerfile", ".env", ".example", ".gitignore", ".lock",
    ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".java", ".go", ".rb", ".php", ".cs", ".rs",
    ".sql", ".proto",
}
_SKIP_DIRS = {
    ".git", ".github", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", ".pytest_cache", ".mypy_cache",
}
_SKIP_FILENAMES = {
    ".DS_Store", "Thumbs.db",
}


# Backward compatibility alias
def run_iac_parser(state: Dict[str, Any]) -> Dict[str, Any]:
    """Backward compatibility: alias for run_stack_analyzer()"""
    return run_stack_analyzer(state)
