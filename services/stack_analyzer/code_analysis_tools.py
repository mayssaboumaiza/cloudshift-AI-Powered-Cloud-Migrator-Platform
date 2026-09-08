"""
code_analysis_tools.py — Static code analysis tools for the Stack Analyzer.

Canonical location: agents/analyzer/ (this file)
Backward-compat re-export: agents/migration_planner/tools_scoring.py

Responsibilities:
  - Parse Python source files with regex to extract imports, SDK calls,
    string constants, embedding model IDs, and MCP server references.
  - Compute a weighted dependency score for a given cloud resource.
  - Detect the primary cloud provider from import strings.
  - Detect the AI/LLM framework used in a project.

All tools are:
  - Decorated with @tool (langchain_core.tools)
  - Return str (JSON-serialised)
  - Zero LLM calls — deterministic regex + pattern matching only
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import tool

import logging
logger = logging.getLogger("Tools")

# ── Cloud detection patterns ──────────────────────────────────────────────────
_AWS_PATTERNS   = ["boto3", "botocore", "boto", "aws_cdk", "langchain_aws"]
_GCP_PATTERNS   = ["google.cloud", "googleapiclient", "vertexai", "google.generativeai", "langchain_google"]
_AZURE_PATTERNS = ["azure", "msazure", "langchain_azure"]

_FRAMEWORK_PATTERNS = {
    "langchain": "langchain",
    "llama_index": "llamaindex",
    "crewai": "crewai",
    "haystack": "haystack",
    "autogen": "autogen",
}

_AGENT_FRAMEWORK_PATTERNS = {
    "langgraph": "langgraph",
    "crewai": "crewai",
    "autogen": "autogen",
    "semantic_kernel": "semantic-kernel",
    "haystack": "haystack",
    "llama_index": "llamaindex",
}

_A2A_PATTERN = re.compile(
    r'(?:A2AClient|A2AServer|agent_to_agent|a2a_protocol)',
    re.IGNORECASE,
)

# Known embedding models with their dimensions and provider
_EMBEDDING_MODELS_DB: dict[str, dict] = {
    "amazon.titan-embed-text-v1":         {"dims": 1536, "type": "proprietary", "cloud": "aws"},
    "amazon.titan-embed-text-v2:0":        {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "cohere.embed-english-v3":            {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "cohere.embed-multilingual-v3":       {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "textembedding-gecko":                {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "models/embedding-001":               {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "text-embedding-004":                 {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "text-multilingual-embedding-002":    {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "text-embedding-ada-002":             {"dims": 1536, "type": "proprietary", "cloud": "openai"},
    "text-embedding-3-small":             {"dims": 1536, "type": "proprietary", "cloud": "openai"},
    "text-embedding-3-large":             {"dims": 3072, "type": "proprietary", "cloud": "openai"},
    "all-MiniLM-L6-v2":                   {"dims": 384,  "type": "open-source", "cloud": "none"},
    "bge-large-en-v1.5":                  {"dims": 1024, "type": "open-source", "cloud": "none"},
    "bge-small-en-v1.5":                  {"dims": 384,  "type": "open-source", "cloud": "none"},
    "bge-m3":                             {"dims": 1024, "type": "open-source", "cloud": "none"},
    "amazon.nova-lite-v1:0":              {"dims": None, "type": "proprietary", "cloud": "aws"},
    "amazon.nova-pro-v1:0":              {"dims": None, "type": "proprietary", "cloud": "aws"},
    "amazon.titan-embed-image-v1":        {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "voyage-3":                           {"dims": 1024, "type": "proprietary", "cloud": "anthropic"},
    "voyage-3-lite":                      {"dims": 512,  "type": "proprietary", "cloud": "anthropic"},
    "e5-large-v2":                        {"dims": 1024, "type": "open-source", "cloud": "none"},
    "multilingual-e5-large":              {"dims": 1024, "type": "open-source", "cloud": "none"},
    "nomic-embed-text-v1":                {"dims": 768,  "type": "open-source", "cloud": "none"},
    "gte-large":                          {"dims": 1024, "type": "open-source", "cloud": "none"},
}


@tool
def ast_parse_file(file_content: str, filename: str) -> str:
    """Parse a Python source file with targeted regex patterns for cloud service detection.

    Uses regex-based analysis (no ast stdlib) to extract imports, API calls with
    variable assignment tracking, and inter-service dependency edges.

    Strategy:
    1. Regex import detection → cloud_import_hints
    2. SDK call patterns (boto3.client, storage.Client, BlobServiceClient...) → var_to_service map
    3. Method calls on tracked vars → populates caller_service for edge building
    4. String scan → embedding model detection

    Args:
        file_content: Raw Python source code string (or Jupyter notebook source).
        filename: Filename for context (e.g. 'app.py').

    Returns:
        JSON string with keys: imports, calls (with caller_service), string_constants,
        embedding_strings, cloud_import_hints, parse_error.
    """
    result: dict[str, Any] = {
        "imports": [],
        "calls": [],
        "string_constants": [],
        "embedding_strings": [],
        "cloud_import_hints": {"aws": [], "gcp": [], "azure": []},
        "parse_error": None,
        "detected_model_ids": [],
        "mcp_servers_detected": [],
    }

    if not file_content or not file_content.strip():
        return json.dumps(result)

    # Strip inline comments to reduce noise in regex matching
    code = re.sub(r'#[^\n]*', '', file_content)

    # ── 1. Import detection ──────────────────────────────────────────────────
    seen_imports: set[str] = set()

    from_re   = re.compile(r'^\s*from\s+([\w.]+)\s+import', re.MULTILINE)
    import_re = re.compile(r'^\s*import\s+([\w.,\s]+)', re.MULTILINE)

    def _register_import(module: str) -> None:
        module = module.strip()
        if not module or module in seen_imports:
            return
        seen_imports.add(module)
        result["imports"].append(module)
        ml = module.lower()
        if any(p in ml for p in _AWS_PATTERNS):
            result["cloud_import_hints"]["aws"].append(module)
        if any(p in ml for p in _GCP_PATTERNS):
            result["cloud_import_hints"]["gcp"].append(module)
        if any(p in ml for p in _AZURE_PATTERNS):
            result["cloud_import_hints"]["azure"].append(module)

    for m in from_re.finditer(code):
        _register_import(m.group(1))

    for m in import_re.finditer(code):
        for part in m.group(1).split(','):
            _register_import(part.strip().split()[0] if part.strip() else "")

    # ── 2. Variable → service tracking ──────────────────────────────────────
    var_to_service: dict[str, str] = {}

    _CALL_BLOCKLIST = frozenset({
        'client', 'resource', 'session', 'response', 'result',
        'data', 'output', 'handler', 'event', 'context',
    })

    _GCP_MODULE_MAP = {
        'storage': 'storage', 'bigquery': 'bigquery',
        'firestore': 'firestore', 'pubsub_v1': 'pubsub',
        'run_v2': 'cloud-run', 'aiplatform': 'vertexai',
    }
    _AZURE_CLASS_MAP = {
        'blobserviceclient': 'blob', 'containerclient': 'blob',
        'cosmosclient': 'cosmos',
        'servicebusclient': 'servicebus',
        'secretclient': 'keyvault',
        'azureopenai': 'openai', 'asyncazureopenai': 'openai',
        'defaultazurecredential': 'identity',
        'managedidentitycredential': 'identity',
        'clientsecretcredential': 'identity',
        'searchclient': 'search',
    }

    def _register_sdk_call(var_name: str, service: str) -> None:
        svc = service.lower().strip()
        if svc and svc not in _CALL_BLOCKLIST:
            var_to_service[var_name] = svc
            result["calls"].append({
                "method": "client",
                "object": "sdk",
                "first_arg": svc,
                "caller_service": svc,
            })

    _boto3_direct  = re.compile(
        r'(\w+)\s*=\s*boto3\.(?:client|resource)\s*\(\s*["\']([a-z][a-z0-9-]*)["\']',
        re.IGNORECASE,
    )
    _session_client = re.compile(
        r'(\w+)\s*=\s*\w+\.(?:client|resource)\s*\(\s*["\']([a-z][a-z0-9-]*)["\']',
        re.IGNORECASE,
    )
    _gcp_client = re.compile(
        r'(\w+)\s*=\s*(storage|bigquery|firestore|pubsub_v1|run_v2|aiplatform)'
        r'\.(?:Client|AsyncClient|PublisherClient|SubscriberClient)\s*\(',
        re.IGNORECASE,
    )
    _azure_client = re.compile(
        r'(\w+)\s*=\s*('
        r'BlobServiceClient|ContainerClient|CosmosClient|'
        r'ServiceBusClient|SecretClient|AzureOpenAI|AsyncAzureOpenAI|'
        r'DefaultAzureCredential|ManagedIdentityCredential|'
        r'ClientSecretCredential|SearchClient'
        r')\s*[.(]',
        re.IGNORECASE,
    )

    for m in _boto3_direct.finditer(code):
        _register_sdk_call(m.group(1), m.group(2))

    for m in _session_client.finditer(code):
        if m.group(1) not in var_to_service:
            _register_sdk_call(m.group(1), m.group(2))

    for m in _gcp_client.finditer(code):
        svc = _GCP_MODULE_MAP.get(m.group(2).lower(), m.group(2).lower())
        _register_sdk_call(m.group(1), svc)

    for m in _azure_client.finditer(code):
        svc = _AZURE_CLASS_MAP.get(m.group(2).lower(), m.group(2).lower())
        _register_sdk_call(m.group(1), svc)

    # ── 3. Method calls on tracked service variables → edges ─────────────────
    _method_call = re.compile(r'\b(\w+)\.(\w+)\s*\(([^)]{0,300})\)', re.DOTALL)
    _skip_methods = frozenset({
        'client', 'resource', 'Session', 'lower', 'upper', 'strip',
        'split', 'join', 'format', 'encode', 'decode', 'get', 'set',
        'append', 'extend', 'update', 'items', 'keys', 'values',
    })
    for m in _method_call.finditer(code):
        obj, method, args_str = m.group(1), m.group(2), m.group(3)
        if obj in var_to_service and method not in _skip_methods:
            caller_svc = var_to_service[obj]
            fa_match = re.search(r'["\']([a-z][a-z0-9_/-]{1,60})["\']', args_str)
            first_arg = fa_match.group(1) if fa_match else ""
            result["calls"].append({
                "method": method,
                "object": obj,
                "first_arg": first_arg,
                "caller_service": caller_svc,
            })

    # ── 4. String constants → embedding model detection ──────────────────────
    _string_val = re.compile(r'["\']([^"\']{5,120})["\']')
    seen_strings: set[str] = set()
    for m in _string_val.finditer(code):
        val = m.group(1)
        if val not in seen_strings:
            seen_strings.add(val)
            result["string_constants"].append(val[:120])
            for model_id in _EMBEDDING_MODELS_DB:
                if model_id in val:
                    result["embedding_strings"].append(
                        {"model_id": model_id, "found_in": val[:80]}
                    )

    # ── 5. LLM model ID detection ─────────────────────────────────────────────
    _LLM_MODEL_PATTERN = re.compile(
        r'["\']('
        r'anthropic\.claude[-\w.:]+|'
        r'amazon\.titan[-\w.:]+|'
        r'amazon\.nova[-\w.:]+|'
        r'cohere\.embed[-\w.:]+|'
        r'cohere\.command[-\w.:]+|'
        r'meta\.llama[-\w.:]+|'
        r'mistral\.mistral[-\w.:]+|'
        r'gemini[-\w.]+|'
        r'text-embedding[-\w.]+|'
        r'text-multilingual-embedding[-\w.]+|'
        r'textembedding-gecko[-\w.]*|'
        r'gpt-[-\w.]+|'
        r'o1[-\w.]*|'
        r'o3[-\w.]*|'
        r'claude-[-\w.]+|'
        r'llama[-\w.:-]+|'
        r'mistral[-\w.:-]+|'
        r'mixtral[-\w.:-]+'
        r')["\']',
        re.IGNORECASE,
    )
    seen_model_ids: set[str] = set()
    for m in _LLM_MODEL_PATTERN.finditer(code):
        mid = m.group(1)
        if mid not in seen_model_ids:
            seen_model_ids.add(mid)
            result["detected_model_ids"].append(mid)

    # ── 6. MCP server detection ───────────────────────────────────────────────
    _MCP_URL_PATTERN = re.compile(
        r'["\']('
        r'https?://[^\s\'"]*(?:mcp|mcp\.claude\.com)[^\s\'"]*|'
        r'(?:localhost|127\.0\.0\.1):\d+(?:/mcp)?[^\s\'"]*'
        r')["\']',
        re.IGNORECASE,
    )
    _MCP_CLASS_PATTERN = re.compile(
        r'(?:MCPStdioClient|FastMCP|MCPServerStdio|'
        r'ClientSession|StdioServerParameters)\s*[(\[]',
    )
    _MCP_IMPORT_PATTERN = re.compile(
        r'(?:from|import)\s+(?:mcp|fastmcp|langchain_mcp|'
        r'mcp\.server|mcp\.client)',
        re.IGNORECASE,
    )
    seen_mcp: set[str] = set()
    for m in _MCP_URL_PATTERN.finditer(code):
        url = m.group(1)
        if url not in seen_mcp:
            seen_mcp.add(url)
            result["mcp_servers_detected"].append({"type": "url", "value": url})

    has_mcp_class  = bool(_MCP_CLASS_PATTERN.search(code))
    has_mcp_import = bool(_MCP_IMPORT_PATTERN.search(code))
    if has_mcp_class or has_mcp_import:
        result["mcp_servers_detected"].append({
            "type": "usage",
            "value": "MCP client/server instantiation detected",
        })

    # Aggregate call frequency per service (deep coupling indicator)
    service_call_counts: dict[str, int] = {}
    for call in result["calls"]:
        svc = call.get("caller_service", "")
        if svc:
            service_call_counts[svc] = service_call_counts.get(svc, 0) + 1
    result["service_call_frequency"] = service_call_counts

    return json.dumps(result)


@tool
def compute_dependency_score(resource: str, imports: list, services: list, api_calls: list) -> str:
    """Compute a weighted dependency score for a cloud resource.

    Weights:
      IMPORT_WEIGHT  = 1  (import declared → intention)
      SERVICE_WEIGHT = 2  (service pattern detected → confirmation)
      CALL_WEIGHT    = 3  (actual API call → real usage)

    Args:
        resource: Service name (e.g. 's3', 'lambda', 'cloud_run').
        imports: List of import module strings found in the file.
        services: List of service name strings detected via import patterns.
        api_calls: List of API call objects (dicts with 'object', 'method', 'first_arg').

    Returns:
        JSON string with: resource, import_score, service_score, call_score,
        weighted_total, confidence (HIGH/MEDIUM/LOW).
    """
    IMPORT_WEIGHT  = 1
    SERVICE_WEIGHT = 2
    CALL_WEIGHT    = 3

    resource_lower = resource.lower()

    import_score  = sum(1 for imp in imports if resource_lower in imp.lower())
    service_score = sum(1 for svc in services if resource_lower in svc.lower())

    def _call_matches_resource(call: dict[str, Any]) -> bool:
        caller_service = str(call.get("caller_service", "")).lower()
        first_arg      = str(call.get("first_arg", "")).lower()
        obj            = str(call.get("object", "")).lower()
        return (
            caller_service == resource_lower or
            first_arg == resource_lower or
            resource_lower in obj
        )

    call_score     = sum(1 for call in api_calls if isinstance(call, dict) and _call_matches_resource(call))
    frequency      = call_score
    frequency_bonus = min(frequency, 5)

    weighted_total = (import_score * IMPORT_WEIGHT +
                      service_score * SERVICE_WEIGHT +
                      call_score * CALL_WEIGHT +
                      frequency_bonus)

    if weighted_total >= 5:
        confidence = "HIGH"
    elif weighted_total >= 2:
        confidence = "MEDIUM"
    elif weighted_total > 0:
        confidence = "LOW"
    else:
        confidence = "NONE"

    return json.dumps({
        "resource": resource,
        "import_score": import_score,
        "service_score": service_score,
        "call_score": call_score,
        "weighted_total": weighted_total,
        "confidence": confidence,
    })


@tool
def detect_cloud_provider(imports_list: list) -> str:
    """Determine the primary cloud provider from a list of Python import strings.

    Uses the same weighted scoring as CloudDetector (weight=1 for imports).

    Args:
        imports_list: List of import module strings (e.g. ['boto3', 'google.cloud.storage']).

    Returns:
        JSON string with: primary_cloud, scores (aws/gcp/azure), confidence,
        all_detected_clouds.
    """
    scores: dict[str, int] = {"aws": 0, "gcp": 0, "azure": 0}

    for imp in imports_list:
        ml = imp.lower()
        if any(p in ml for p in _AWS_PATTERNS):
            scores["aws"] += 1
        if any(p in ml for p in _GCP_PATTERNS):
            scores["gcp"] += 1
        if any(p in ml for p in _AZURE_PATTERNS):
            scores["azure"] += 1

    if max(scores.values()) == 0:
        primary_cloud = "unknown"
        confidence    = "NONE"
    else:
        primary_cloud = max(scores, key=lambda k: scores[k])
        top   = scores[primary_cloud]
        total = sum(scores.values())
        pct   = (top / total * 100) if total else 0
        confidence = "HIGH" if pct >= 70 else ("MEDIUM" if pct >= 40 else "LOW")

    return json.dumps({
        "primary_cloud": primary_cloud,
        "scores": scores,
        "confidence": confidence,
        "all_detected_clouds": [c for c, s in scores.items() if s > 0],
    })


@tool
def detect_ai_framework(imports_list: list) -> str:
    """Detect the AI/LLM framework used in a project from its import strings.

    Detects: langchain, llamaindex, crewai, haystack, autogen, langgraph,
    semantic-kernel, and vector DBs (chromadb, pinecone, faiss, weaviate, qdrant).
    Also detects agent frameworks, MCP usage, and A2A protocol.

    Args:
        imports_list: List of import module strings.

    Returns:
        JSON string with: framework (str|None), llm_provider (str|None),
        vector_db (str|None), all_matches, agent_frameworks, mcp_detected, a2a_detected.
    """
    framework      = None
    llm_provider   = None
    vector_db      = None
    all_matches:      list[str] = []
    agent_frameworks: list[str] = []
    mcp_detected  = False
    a2a_detected  = False

    for imp in imports_list:
        ml = imp.lower()

        if framework is None:
            for pattern, name in _FRAMEWORK_PATTERNS.items():
                if pattern in ml:
                    framework = name
                    all_matches.append(f"framework:{name}")
                    break

        for pattern, name in _AGENT_FRAMEWORK_PATTERNS.items():
            pat_variants = [pattern, pattern.replace("_", "-"), pattern.replace("-", "_")]
            if any(v in ml for v in pat_variants):
                if name not in agent_frameworks:
                    agent_frameworks.append(name)
                    all_matches.append(f"agent_fw:{name}")

        if "bedrock" in ml and llm_provider is None:
            llm_provider = "bedrock"
            all_matches.append("llm:bedrock")
        elif "openai" in ml and llm_provider is None:
            llm_provider = "openai"
            all_matches.append("llm:openai")
        elif ("vertexai" in ml or "gemini" in ml) and llm_provider is None:
            llm_provider = "google"
            all_matches.append("llm:google")
        elif "anthropic" in ml and llm_provider is None:
            llm_provider = "anthropic"
            all_matches.append("llm:anthropic")
        elif "azure" in ml and llm_provider is None:
            llm_provider = "azure_openai"
            all_matches.append("llm:azure_openai")

        if vector_db is None:
            if "chroma" in ml:
                vector_db = "chromadb"
                all_matches.append("vectordb:chromadb")
            elif "pinecone" in ml:
                vector_db = "pinecone"
                all_matches.append("vectordb:pinecone")
            elif "faiss" in ml:
                vector_db = "faiss"
                all_matches.append("vectordb:faiss")
            elif "weaviate" in ml:
                vector_db = "weaviate"
                all_matches.append("vectordb:weaviate")
            elif "qdrant" in ml:
                vector_db = "qdrant"
                all_matches.append("vectordb:qdrant")

        if not mcp_detected:
            if any(tok in ml for tok in ("mcp", "fastmcp", "langchain_mcp")):
                mcp_detected = True
                all_matches.append("mcp:detected")

        if not a2a_detected and _A2A_PATTERN.search(imp):
            a2a_detected = True
            all_matches.append("a2a:detected")

    return json.dumps({
        "framework": framework,
        "llm_provider": llm_provider,
        "vector_db": vector_db,
        "all_matches": all_matches,
        "agent_frameworks": agent_frameworks,
        "mcp_detected": mcp_detected,
        "a2a_detected": a2a_detected,
    })
