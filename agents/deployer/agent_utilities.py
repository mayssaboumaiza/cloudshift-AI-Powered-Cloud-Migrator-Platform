"""
Shared utilities, constants, and Pydantic models for cloud-migrator tools.
"""
import concurrent.futures
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field
    from langchain_core.output_parsers import PydanticOutputParser
    _HAS_PYDANTIC_PARSER = True
except ImportError:
    _HAS_PYDANTIC_PARSER = False

logger = logging.getLogger("Tools")

# ─────────────────────────────────────────────────────────────────────────────
# Paths (resolved relative to this file — works in Docker and locally)
# ─────────────────────────────────────────────────────────────────────────────
_AGENTS_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_AGENTS_DIR)

_out_env_shared = os.environ.get("MIGRATION_OUTPUT_DIR", "")
OUTPUT_DIR = _out_env_shared if os.path.isabs(_out_env_shared) else os.path.join(
    _PROJECT_ROOT, _out_env_shared or os.path.join("output", "migrated_app")
)
TEMPLATES_DIR = os.path.join(_AGENTS_DIR, "deployer", "templates")

# ─────────────────────────────────────────────────────────────────────────────
# Shared utilities (exported for agents — avoids code duplication)
# ─────────────────────────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a text string (LLM output parser).

    Tries in order:
    1. Direct JSON parse of the whole string.
    2. First ```json ... ``` fenced block.
    3. First {...} block found with regex (size-limited to 100k chars).

    Returns None if no valid JSON object found.
    """
    if not text:
        return None
    # 1. Direct parse
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass
    # 2. Fenced code block
    m = re.search(r"```(?:json)?\s*(\{[^`]{0,100000}?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    # 3. First braces block (limit to 100k chars to avoid ReDoS)
    m = re.search(r"\{.{0,100000}\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# YAML helper
# ─────────────────────────────────────────────────────────────────────────────
def _load_yaml(path: str) -> dict:
    """Load a YAML file. Returns empty dict on any error, with logging."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.debug(f"YAML mapping not found: {path}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load YAML {path}: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models for LLM enrichment output (MOD 3)
# ─────────────────────────────────────────────────────────────────────────────
if _HAS_PYDANTIC_PARSER:
    class EnrichedServiceOutput(BaseModel):
        """Schema for a single service enrichment from the LLM (source-side only)."""
        lock_in_level: str = Field(description="LOW, MEDIUM, or HIGH — source-side SDK coupling")
        effort_estimate: str = Field(description="e.g. '2-4h', '1-2 days' — general order of magnitude")
        migration_risks: list[str] = Field(default_factory=list)

    class CodePatternOutput(BaseModel):
        """Pattern d'accès d'un service détecté par Agent 01 Phase 3."""
        access_patterns: list[str] = Field(default_factory=list)
        data_volume_hint: str = Field(default="medium")
        stateful: bool = Field(default=False)
        sdk_patterns: list[str] = Field(default_factory=list)
        transaction_usage: bool = Field(default=False)
        event_coupling: bool = Field(default=False)

    class LLMModelFound(BaseModel):
        """A single LLM model detected in the source code."""
        model_id: str = Field(default="")
        cloud: str = Field(default="unknown")
        type: str = Field(default="proprietary")
        embedding_dims: int | None = Field(default=None)

    class EmbeddingModelFound(BaseModel):
        """Embedding model detected in the source code."""
        model_id: str = Field(default="")
        dims: int | None = Field(default=None)
        type: str = Field(default="proprietary")
        cloud: str = Field(default="unknown")

    class MCPServerFound(BaseModel):
        """MCP server detected in the source code."""
        file: str = Field(default="")
        transport: str = Field(default="stdio")
        cloud_apis_detected: list[str] = Field(default_factory=list)
        portable: bool = Field(default=False)

    class AIStackAssessment(BaseModel):
        """Detection-only AI stack assessment (source-side, no target reasoning)."""
        llm_models_found: list[LLMModelFound] = Field(default_factory=list)
        embedding_model_found: EmbeddingModelFound | None = Field(default=None)
        mcp_servers_found: list[MCPServerFound] = Field(default_factory=list)

    class DependencyGraphOutput(BaseModel):
        """Validated schema for the LLM enrichment response.

        Ensures the LLM returns all required fields with correct types.
        Used by _llm_enrich_analysis() via PydanticOutputParser.

        NOTE: migration_strategy_global, total_effort_estimate et
        highest_risk_service sont calcules par Agent 03, pas ici.
        migration_hint et recommended_strategy appartiennent à Agent 02,
        pas à Agent 01 qui ne connaît pas le cloud cible.
        """
        architecture_summary: str = Field(
            description="2-3 sentence description of the app architecture",
        )
        enriched_services: dict[str, EnrichedServiceOutput] = Field(
            default_factory=dict,
        )
        code_patterns: dict[str, CodePatternOutput] = Field(default_factory=dict)
        cloud_source_validation: dict = Field(default_factory=dict)
        ai_stack_assessment: AIStackAssessment | None = Field(default=None)

    enrichment_parser = PydanticOutputParser(pydantic_object=DependencyGraphOutput)
else:
    enrichment_parser = None  # type: ignore[assignment]
    DependencyGraphOutput = None  # type: ignore[assignment,misc]


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 02 TOOLS — Pydantic models (scoring + savings)
# ─────────────────────────────────────────────────────────────────────────────

if _HAS_PYDANTIC_PARSER:
    from typing import List as _List, Optional as _Optional

    class ServiceCandidate(BaseModel):
        """Un candidat de migration avec scoring composite complet."""
        service_name: str = Field(description="Nom du service cible")
        cloud_provider: str = Field(description="Cloud cible : aws | gcp | azure")
        cost_score: float = Field(default=0.0, description="0-100, 100 = le moins cher")
        performance_score: float = Field(default=0.0, description="0-100, depuis SLA + latence p99")
        modernity_score: float = Field(default=60.0, description="0-100, depuis compare_service_generations")
        effort_score: float = Field(default=60.0, description="0-100, 100 = effort minimal (Rehost)")
        composite_score: float = Field(default=0.0, description="Score pondéré final")
        monthly_cost_estimate: float = Field(default=0.0, description="Coût mensuel estimé en USD")
        pricing_source: str = Field(default="estimate", description="live_api | cache | estimate")
        migration_complexity: str = Field(default="medium", description="low | medium | high")
        recommended_discount: _Optional[str] = Field(default=None, description="reserved_1y | committed_3y | None")
        discount_savings_pct: float = Field(default=0.0, description="% d'économie si discount appliqué")

    class SavingsOpportunity(BaseModel):
        """Une opportunité de réduction de coût pour un service cible."""
        service: str
        cloud: str
        base_monthly: float
        optimized_monthly: float
        saving_pct: float
        mechanism: str = Field(description="reserved_1y | committed_3y | spot | sustained_auto | hybrid_benefit")
        condition: str = Field(description="Description lisible de la condition")

    class MigrationResourceEnriched(BaseModel):
        """Ressource de migration enrichie avec scoring et code_patterns d'Agent01."""
        source_service: str
        source_cloud: str
        strategy: str = Field(description="7R : REHOST | REPLATFORM | REFACTOR | REPURCHASE | RETIRE | RETAIN | RELOCATE")
        recommended_target: _Optional[ServiceCandidate] = None
        all_candidates: _List[ServiceCandidate] = Field(default_factory=list)
        savings_opportunities: _List[SavingsOpportunity] = Field(default_factory=list)
        cloud_confirmed: bool = Field(default=True)
        code_patterns: dict = Field(default_factory=dict)
        terraform_hints: dict = Field(default_factory=dict)
        migration_notes: str = Field(default="")

else:
    ServiceCandidate = None           # type: ignore[assignment,misc]
    SavingsOpportunity = None         # type: ignore[assignment,misc]
    MigrationResourceEnriched = None  # type: ignore[assignment,misc]


# ─────────────────────────────────────────────────────────────────────────────
# Internal constants (extracted from CloudDetector / TerraformAnalyzer)
# ─────────────────────────────────────────────────────────────────────────────
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
    # Extended models (Modification 6)
    "amazon.nova-lite-v1:0":              {"dims": None, "type": "proprietary", "cloud": "aws"},
    "amazon.nova-pro-v1:0":               {"dims": None, "type": "proprietary", "cloud": "aws"},
    "amazon.titan-embed-image-v1":        {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "voyage-3":                           {"dims": 1024, "type": "proprietary", "cloud": "anthropic"},
    "voyage-3-lite":                      {"dims": 512,  "type": "proprietary", "cloud": "anthropic"},
    "e5-large-v2":                        {"dims": 1024, "type": "open-source", "cloud": "none"},
    "multilingual-e5-large":              {"dims": 1024, "type": "open-source", "cloud": "none"},
    "nomic-embed-text-v1":                {"dims": 768,  "type": "open-source", "cloud": "none"},
    "gte-large":                          {"dims": 1024, "type": "open-source", "cloud": "none"},
}

_AWS_PATTERNS  = ["boto3", "botocore", "boto", "aws_cdk", "langchain_aws"]
_GCP_PATTERNS  = ["google.cloud", "googleapiclient", "vertexai", "google.generativeai", "langchain_google"]
_AZURE_PATTERNS = ["azure", "msazure", "langchain_azure"]

_FRAMEWORK_PATTERNS = {
    "langchain": "langchain",
    "llama_index": "llamaindex",
    "crewai": "crewai",
    "haystack": "haystack",
    "autogen": "autogen",
}

_AWS_SERVICES_MAP = {
    "s3": "storage", "dynamodb": "database", "lambda": "compute",
    "sqs": "messaging", "sns": "messaging", "bedrock": "ai",
    "bedrock-runtime": "ai", "bedrock-agent": "ai", "bedrock-agent-runtime": "ai",
    "opensearch": "search", "opensearchserverless": "search", "sagemaker": "ai",
    "ec2": "compute", "rds": "database", "eks": "compute",
    "cloudwatch": "monitoring", "iam": "security", "sts": "iam",
    "secretsmanager": "security", "ecr": "container", "ecs": "compute",
    "cloudfront": "networking", "route53": "networking",
    "api_gateway": "networking", "vpc": "networking",
}

_GCP_SERVICES_MAP = {
    "storage": "storage", "bigquery": "analytics", "firestore": "database",
    "pubsub": "messaging", "run": "compute", "functions": "compute",
    "aiplatform": "ai", "vertexai": "ai", "sql": "database",
    "dns": "networking", "container": "compute",
}

_AZURE_SERVICES_MAP = {
    "blob": "storage", "cosmos": "database", "functions": "compute",
    "servicebus": "messaging", "inference": "ai", "openai": "ai",
    "keyvault": "security", "search": "search",
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


# ─────────────────────────────────────────────────────────────────────────────
# SEMGREP — Structural cloud SDK detection (Layer 2 benchmark)
# ─────────────────────────────────────────────────────────────────────────────

_SEMGREP_RULES_DIR = os.path.join(_AGENTS_DIR, "semgrep_rules")
_SEMGREP_TIMEOUT = 30  # seconds

# Service names that are SDK internals, not real cloud services.
# Semgrep rule-id parsing can produce these as false positives
# (e.g. 'aws-boto3-session' → 'boto3-session', 'boto3').
_SEMGREP_SERVICE_BLOCKLIST = frozenset({
    "boto3", "boto3-session", "session", "client", "resource",
    "botocore", "sdk", "boto",
})

try:
    _semgrep_check = subprocess.run(
        ["semgrep", "--version"],
        capture_output=True, text=True, timeout=5,
    )
    _HAS_SEMGREP = _semgrep_check.returncode == 0
except Exception:
    _HAS_SEMGREP = False


def _semgrep_analyze(file_content: str, filename: str) -> dict[str, Any]:
    """Run Semgrep structural analysis on a Python file for cloud SDK detection.

    Writes content to a temp file, runs semgrep with cloud_sdk.yaml rules,
    and parses the JSON output. More reliable than regex for detecting
    patterns like boto3.client('s3'), storage.Client(), BlobServiceClient().

    Args:
        file_content: Python source code string.
        filename: Original filename (for temp file extension).

    Returns:
        Dict with keys: cloud_services (list), api_patterns (list),
        cloud_scores (dict), findings_count (int).
        Returns empty dict if semgrep is unavailable or fails.
    """
    if not _HAS_SEMGREP or not file_content:
        return {}

    rules_path = os.path.join(_SEMGREP_RULES_DIR, "cloud_sdk.yaml")
    if not os.path.exists(rules_path):
        logger.debug("_semgrep_analyze: cloud_sdk.yaml rules not found")
        return {}

    result: dict[str, Any] = {
        "cloud_services": [],
        "api_patterns": [],
        "cloud_scores": {"aws": 0, "gcp": 0, "azure": 0},
        "findings_count": 0,
    }

    suffix = Path(filename).suffix or ".py"
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            # PYTHONUTF8=1 required on Windows to avoid cp1252 codec errors
            env = {**os.environ, "PYTHONUTF8": "1"}
            proc = subprocess.run(
                [
                    "semgrep", "scan",
                    "--config", rules_path,
                    "--json",
                    "--quiet",
                    "--no-git-ignore",
                    tmp_path,
                ],
                capture_output=True, text=True,
                timeout=_SEMGREP_TIMEOUT,
                env=env,
            )

            if proc.returncode not in (0, 1):
                # returncode 1 = findings found, 0 = no findings
                logger.debug(f"_semgrep_analyze: semgrep exit {proc.returncode}")
                return {}

            output = json.loads(proc.stdout) if proc.stdout else {}
            findings = output.get("results", [])
            result["findings_count"] = len(findings)

            seen_services: set[str] = set()
            for finding in findings:
                extra = finding.get("extra", {})
                meta = extra.get("metadata", {})
                cloud = meta.get("cloud", "")
                service = meta.get("service", "")
                category = meta.get("category", "")
                detection_type = meta.get("detection_type", "")
                rule_id = finding.get("check_id", "")
                message = extra.get("message", "")
                matched_code = extra.get("lines", "")

                # Resolve service name — 3 strategies (priority order):
                # 1. metadata.service (set explicitly in YAML, e.g. 'blob', 'cosmos')
                # 2. Extract from message after last ':' (e.g. "client: 's3'" → 's3')
                # 3. Extract semantic part from rule_id (last meaningful segment)
                if not service:
                    # Strategy 2: parse message "...client: 's3'" → 's3'
                    msg_match = re.search(r":\s*['\"]?([a-z][a-z0-9_-]+)['\"]?$",
                                         message, re.IGNORECASE)
                    if msg_match:
                        service = msg_match.group(1).lower()
                if not service:
                    # Strategy 3: rule_id last segment after known prefixes
                    rule_suffix = rule_id.split(".")[-1]  # e.g. 'aws-boto3-client'
                    parts = rule_suffix.split("-")
                    # Drop cloud prefix ('aws', 'gcp', 'azure') and sdk name
                    svc_parts = [p for p in parts[1:] if p not in ("client", "import")]
                    service = "-".join(svc_parts) if svc_parts else rule_suffix

                # Update cloud scores
                if cloud in result["cloud_scores"]:
                    # api_call findings weighted 3, imports weighted 1 (matches AST weights)
                    weight = 3 if detection_type == "api_call" else 1
                    result["cloud_scores"][cloud] += weight

                # Track unique services — filter SDK internals (false positives)
                if service and service not in seen_services and service not in _SEMGREP_SERVICE_BLOCKLIST:
                    seen_services.add(service)
                    result["cloud_services"].append({
                        "service": service,
                        "cloud": cloud,
                        "category": category,
                        "source": "semgrep",
                    })

                result["api_patterns"].append({
                    "rule_id": rule_id,
                    "cloud": cloud,
                    "service": service,
                    "detection_type": detection_type,
                    "line": finding.get("start", {}).get("line", 0),
                    "matched_code": matched_code[:120],
                })

        finally:
            os.unlink(tmp_path)

    except subprocess.TimeoutExpired:
        logger.warning(f"_semgrep_analyze: timeout after {_SEMGREP_TIMEOUT}s")
        return {}
    except Exception as exc:
        logger.debug(f"_semgrep_analyze: failed: {exc}")
        return {}

    logger.info(
        f"_semgrep_analyze({filename}): {result['findings_count']} findings, "
        f"services={[s['service'] for s in result['cloud_services']]}"
    )
    return result


def _safe_list(value: Any) -> list:
    """Normalise une valeur en list (dict natif ou JSON string)."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("files", "python_files", "result", "data", "items", "content", "paths", "file_list"):
            if isinstance(value.get(key), list):
                return value[key]
        for k, v in value.items():
            if isinstance(v, list):
                logger.warning(f"_safe_list: clé inattendue '{k}' utilisée")
                return v
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _safe_dict(value: Any) -> dict:
    """Normalise une valeur en dict (gère str JSON et dict natif)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _safe_str(value: Any) -> str:
    """Extrait le contenu texte d'une valeur (str ou dict{"content": ...})."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("content", value.get("result", ""))
    return str(value) if value else ""


def _extract_python_from_notebook(content: str) -> str:
    """Extrait uniquement les cellules 'code' d'un notebook .ipynb."""
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


def _compute_weights(constraints: dict) -> dict:
    """Calcule les poids de scoring selon les contraintes utilisateur.

    Args:
        constraints: {"budget": "strict|moderate|flexible",
                      "timeline": "ASAP|3_months|6_months|flexible",
                      "risk_tolerance": "low|medium|high"}

    Returns:
        Dict {"cost", "performance", "effort", "modernity"} — somme = 1.0
    """
    budget    = (constraints.get("budget") or "moderate").lower()
    timeline  = (constraints.get("timeline") or "3_months").lower()
    risk      = (constraints.get("risk_tolerance") or "medium").lower()

    if budget == "strict":
        return {"cost": 0.50, "performance": 0.15, "effort": 0.20, "modernity": 0.15}
    if timeline == "asap":
        return {"cost": 0.20, "performance": 0.20, "effort": 0.45, "modernity": 0.15}
    if risk == "low":
        return {"cost": 0.25, "performance": 0.25, "effort": 0.35, "modernity": 0.15}
    # balanced (moderate budget, 3_months, medium risk)
    return {"cost": 0.30, "performance": 0.25, "effort": 0.25, "modernity": 0.20}
