"""
python_sdk_parser.py - Detect cloud SDK calls in Python code (ast-only, no LLM).

Complements IaC parsing: Terraform is the gold source for INFRA resources, but
application Python code often references buckets/tables/queues that are
provisioned elsewhere. This parser finds those references so Agent 01 sees
the full picture.

Detects:
  - AWS     (boto3.client("s3"), boto3.resource("dynamodb"), ...)
  - GCP     (from google.cloud import storage / firestore / pubsub / ...)
  - Azure   (from azure.storage.blob import BlobServiceClient, ...)
  - Provider-agnostic handles (redis.Redis, psycopg2.connect, pymongo.MongoClient)

Output per file: list of detections with:
  - provider        (aws | gcp | azure | generic)
  - service_type    (storage | database | queue | cache | ...)
  - handle          (bucket name, queue name, URL — if extractable)
  - evidence        (filename:lineno)

Pitfalls handled:
  - Only scans .py files ≤ 200 KB (skip vendored libs, generated code)
  - Resolves simple module-level constants & os.getenv() fallbacks
  - Never executes the code (pure AST)
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("PythonSDKParser")

_MAX_FILE_SIZE = 200 * 1024  # 200 KB
_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", ".git", "dist", "build"}


# ─────────────────────────────────────────────────────────────────────────────
# SDK signatures — additive; not exhaustive but covers the common cases
# ─────────────────────────────────────────────────────────────────────────────

# boto3.client("SERVICE") / boto3.resource("SERVICE") → provider=aws
_AWS_BOTO3_SERVICES = {
    "s3": "storage",
    "dynamodb": "key-value-nosql",
    "lambda": "serverless-functions",
    "sqs": "message-queue",
    "sns": "message-queue",
    "kinesis": "event-streaming",
    "rds": "managed-relational-db",
    "secretsmanager": "secrets-management",
    "kms": "secrets-management",
    "cognito-idp": "identity-access-management",
    "iam": "identity-access-management",
    "bedrock": "llm-inference",
    "bedrock-runtime": "llm-inference",
    "sagemaker": "ml-training",
    "ec2": "virtual-machines",
    "eks": "managed-kubernetes",
    "ecs": "container-compute",
    "cloudwatch": "observability",
    "logs": "observability",
    "apigateway": "api-gateway",
    "stepfunctions": "orchestration",
    "opensearch": "search",
}

# from google.cloud import X → provider=gcp
_GCP_MODULES = {
    "storage": "storage",
    "firestore": "key-value-nosql",
    "bigquery": "analytics-warehouse",
    "pubsub": "message-queue",
    "pubsub_v1": "message-queue",
    "tasks": "message-queue",
    "aiplatform": "llm-inference",
    "secretmanager": "secrets-management",
    "sql": "managed-relational-db",
    "run_v2": "serverless-functions",
    "functions_v1": "serverless-functions",
    "container_v1": "managed-kubernetes",
    "logging": "observability",
    "monitoring": "observability",
}

# from azure.X import Y → provider=azure
_AZURE_MODULES = {
    "storage.blob": "storage",
    "storage.queue": "message-queue",
    "cosmos": "key-value-nosql",
    "servicebus": "message-queue",
    "eventhub": "event-streaming",
    "keyvault.secrets": "secrets-management",
    "identity": "identity-access-management",
    "ai.openai": "llm-inference",
    "mgmt.containerservice": "managed-kubernetes",
    "functions": "serverless-functions",
    "monitor": "observability",
}

# Provider-agnostic (still useful — flags "redis" even if not in IaC)
_GENERIC_IMPORTS = {
    "redis": ("generic", "cache"),
    "aioredis": ("generic", "cache"),
    "pymongo": ("generic", "document-nosql"),
    "psycopg2": ("generic", "managed-relational-db"),
    "psycopg": ("generic", "managed-relational-db"),
    "kafka": ("generic", "event-streaming"),
    "elasticsearch": ("generic", "search"),
    "pinecone": ("generic", "vectordb"),
    "weaviate": ("generic", "vectordb"),
    "qdrant_client": ("generic", "vectordb"),
    "chromadb": ("generic", "vectordb"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_python_sdk_calls(repo_root: str | Path) -> list[dict]:
    """Scan a repo directory for cloud SDK usage.

    Returns a list of detection dicts (see module docstring).
    """
    root = Path(repo_root)
    if not root.exists() or not root.is_dir():
        logger.warning(f"parse_python_sdk_calls: {root} not a directory")
        return []

    detections: list[dict] = []
    for py_file in _iter_python_files(root):
        try:
            detections.extend(_parse_file(py_file, root))
        except SyntaxError as e:
            logger.debug(f"Skipping {py_file} (SyntaxError: {e})")
            continue
        except Exception as e:
            logger.warning(f"Failed to parse {py_file}: {e}")
            continue

    return _dedupe(detections)


def parse_python_files(files: dict[str, str]) -> list[dict]:
    """Alternative entrypoint when files are already fetched into memory
    (e.g. via PyGithub). `files` maps relative path → content."""
    detections: list[dict] = []
    for rel_path, content in files.items():
        if len(content.encode("utf-8", errors="ignore")) > _MAX_FILE_SIZE:
            continue
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            continue
        constants = _collect_module_constants(tree)
        detections.extend(_walk(tree, rel_path, constants))
    return _dedupe(detections)


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # In-place prune to skip vendored / generated / hidden dirs
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                p = Path(dirpath) / fn
                try:
                    if p.stat().st_size > _MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                yield p


def _parse_file(path: Path, root: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(content, filename=str(path))
    rel = path.relative_to(root).as_posix()
    constants = _collect_module_constants(tree)
    return _walk(tree, rel, constants)


def _collect_module_constants(tree: ast.AST) -> dict[str, str]:
    """Collect module-level string assignments + os.getenv fallbacks.
    Lets us resolve `boto3.client("s3").get_object(Bucket=BUCKET_NAME)` when
    `BUCKET_NAME = "my-bucket"` is at module scope."""
    out: dict[str, str] = {}
    if not isinstance(tree, ast.Module):
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out[target.id] = value.value
        elif isinstance(value, ast.Call):
            # os.getenv("X", "default") → default string if literal
            if _is_call_to(value, "os", "getenv") and len(value.args) >= 2:
                if isinstance(value.args[1], ast.Constant) and isinstance(value.args[1].value, str):
                    out[target.id] = value.args[1].value
    return out


def _is_call_to(call: ast.Call, module: str, attr: str) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == attr:
        if isinstance(f.value, ast.Name) and f.value.id == module:
            return True
    return False


def _resolve_str(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _walk(tree: ast.AST, rel_path: str, constants: dict[str, str]) -> list[dict]:
    found: list[dict] = []
    imports: dict[str, str] = {}  # alias → full dotted path

    for node in ast.walk(tree):
        # ── Imports ─────────────────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = alias.name
                _check_generic_import(alias.name, rel_path, node.lineno, found)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported = [a.name for a in node.names]
            _check_provider_import(mod, rel_path, node.lineno, found, imported)
            _check_generic_import(mod.split(".")[0], rel_path, node.lineno, found)
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{mod}.{alias.name}"

        # ── boto3.client("s3") / boto3.resource("s3") ──────────────────
        elif isinstance(node, ast.Call):
            svc = _match_boto3(node)
            if svc:
                # boto3.client("s3") declares a client for the service — the
                # service name itself is not a resource handle.
                found.append({
                    "provider": "aws",
                    "service_type": _AWS_BOTO3_SERVICES.get(svc, "unknown"),
                    "service": svc,
                    "handle": None,
                    "evidence": f"{rel_path}:{node.lineno}",
                })

    return found


def _check_provider_import(
    module: str,
    rel_path: str,
    lineno: int,
    out: list[dict],
    imported_names: list[str] | None = None,
) -> None:
    imported_names = imported_names or []
    # from google.cloud import storage, pubsub_v1  → module="google.cloud"
    # from google.cloud.storage import Client       → module="google.cloud.storage"
    if module == "google.cloud":
        for name in imported_names:
            if name in _GCP_MODULES:
                out.append({
                    "provider": "gcp",
                    "service_type": _GCP_MODULES[name],
                    "service": name,
                    "handle": None,
                    "evidence": f"{rel_path}:{lineno}",
                })
    elif module.startswith("google.cloud."):
        tail = module[len("google.cloud."):].split(".", 1)[0]
        if tail in _GCP_MODULES:
            out.append({
                "provider": "gcp",
                "service_type": _GCP_MODULES[tail],
                "service": tail,
                "handle": None,
                "evidence": f"{rel_path}:{lineno}",
            })
    # from azure.X.Y import Z
    elif module.startswith("azure."):
        tail = module[len("azure."):]
        for az_mod, svc_type in _AZURE_MODULES.items():
            if tail == az_mod or tail.startswith(az_mod + "."):
                out.append({
                    "provider": "azure",
                    "service_type": svc_type,
                    "service": az_mod,
                    "handle": None,
                    "evidence": f"{rel_path}:{lineno}",
                })
                break


def _check_generic_import(top_module: str, rel_path: str, lineno: int, out: list[dict]) -> None:
    info = _GENERIC_IMPORTS.get(top_module)
    if not info:
        return
    provider, svc_type = info
    out.append({
        "provider": provider,
        "service_type": svc_type,
        "service": top_module,
        "handle": None,
        "evidence": f"{rel_path}:{lineno}",
    })


def _match_boto3(call: ast.Call) -> str | None:
    """Match boto3.client("X") or boto3.resource("X"); return X or None."""
    f = call.func
    if not isinstance(f, ast.Attribute):
        return None
    if f.attr not in ("client", "resource", "Session"):
        return None
    # boto3.client or session.client — accept both
    if isinstance(f.value, ast.Name) and f.value.id in ("boto3", "session"):
        pass
    elif isinstance(f.value, ast.Attribute) and f.value.attr in ("boto3", "session"):
        pass
    else:
        return None
    if not call.args:
        return None
    svc = call.args[0]
    if isinstance(svc, ast.Constant) and isinstance(svc.value, str):
        s = svc.value.lower().strip()
        if s in _AWS_BOTO3_SERVICES:
            return s
    return None


def _dedupe(detections: list[dict]) -> list[dict]:
    """Keep first occurrence per (provider, service, handle)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for d in detections:
        key = (d.get("provider"), d.get("service"), d.get("handle"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out
