"""
sa_ai_analyzer.py — AIPythonAnalyzer: 100% local Python AI stack detection.

Analyses Python source code with stdlib ast to detect:
  Level 1 — AI provider imports (LangChain, Bedrock, VertexAI, etc.)
  Level 2 — Class instantiation (ChatOpenAI, BedrockEmbeddings, FAISS, etc.)
  Level 3 — String constants (model names, endpoint strings)

No LLM calls — zero network traffic at analysis time.
"""
from __future__ import annotations
import ast
import logging
from typing import Any
from services.stack_analyzer.constants import (
    AI_PROVIDER_IMPORTS, AI_PROVIDER_PREFIXES,
    AI_VECTOR_IMPORTS, AI_CLASSES,
)

logger = logging.getLogger("StackAnalyzer")

class AIPythonAnalyzer:
    """Analyze Python files for AI stack detection (LLM, embeddings, vector stores).

    100% local analysis using stdlib (ast, os, pathlib).
    Code source is NEVER transmitted to external systems.
    """

    def __init__(self):
        self.imports: dict[str, list[str]] = {}
        self.instantiations: list[dict] = []
        self.constants: dict[str, str] = {}

    def scan_imports(self, source_code: str) -> dict[str, list[str]]:
        """Level 1: Detect AI provider imports using AST.

        Returns dict mapping provider type to list of module names.
        Checks exact matches in AI_PROVIDER_IMPORTS / AI_VECTOR_IMPORTS first,
        then falls back to prefix matching via AI_PROVIDER_PREFIXES to catch
        sub-module imports (e.g. "from transformers.models.llama import ...").
        """
        imports: dict[str, list[str]] = {
            "llm_providers": [],
            "vector_stores": [],
            "generic": [],
        }
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                modules_to_check: list[str] = []
                if isinstance(node, ast.Import):
                    modules_to_check = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules_to_check = [node.module or ""]

                for module in modules_to_check:
                    if not module:
                        continue
                    if module in AI_PROVIDER_IMPORTS:
                        imports["llm_providers"].append(module)
                    elif module in AI_VECTOR_IMPORTS:
                        imports["vector_stores"].append(module)
                    else:
                        # Prefix fallback — catches sub-module imports
                        matched = False
                        for prefix, provider_type in AI_PROVIDER_PREFIXES.items():
                            if module.startswith(prefix):
                                imports["llm_providers"].append(f"{module}→{provider_type}")
                                matched = True
                                break
                        if not matched:
                            # Detect generic HTTP-based LLM usage patterns
                            # (e.g. requests.post to openai/anthropic/etc. endpoints)
                            _LLM_API_HINTS = {
                                "openai": "openai_sdk",
                                "anthropic": "anthropic_sdk",
                                "mistral": "mistral_sdk",
                                "cohere": "cohere_sdk",
                                "groq": "groq_sdk",
                                "together": "together_sdk",
                            }
                            for hint, provider in _LLM_API_HINTS.items():
                                if hint in module.lower():
                                    imports["generic"].append(f"{module}→{provider}")
                                    break
        except SyntaxError as e:
            logger.debug(f"scan_imports: SyntaxError — {e}")
        return imports

    def scan_instantiations(self, source_code: str, filename: str = "", constants: dict | None = None) -> list[dict]:
        """Level 2: Detect AI class instantiations (ChatBedrock, ChatVertexAI, etc.).

        Also detects boto3.client("bedrock*") / boto3.client("sagemaker*") calls
        so that repos using raw Bedrock SDK are recognised as LLM users.
        constants: module-level constants from resolve_constants() — used to resolve model_id variables.
        """
        instantiations = []
        constants = constants or {}
        # Bedrock service names that indicate LLM usage
        _BEDROCK_SERVICES = {
            "bedrock-runtime":          "aws_bedrock_claude",
            "bedrock":                  "aws_bedrock",
            "bedrock-agent-runtime":    "aws_bedrock_agent",
            "bedrock-agent":            "aws_bedrock_agent",
            "sagemaker-runtime":        "aws_sagemaker",
        }
        # Module-level constants that likely hold a Bedrock model ID
        _MODEL_ID_VARS = {"_MODEL_ID", "MODEL_ID", "model_id", "BEDROCK_MODEL_ID", "MODEL", "model"}
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # ── boto3.client("bedrock-runtime") detection ──────────────
                    if (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("client", "resource")
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in ("boto3", "session")
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        svc = node.args[0].value.lower()
                        if svc in _BEDROCK_SERVICES:
                            # Try to resolve model_id from module constants
                            resolved_model = next(
                                (v for k, v in constants.items() if k in _MODEL_ID_VARS and not v.startswith("<env:")),
                                ""
                            )
                            instantiations.append({
                                "class": f"boto3.client({svc!r})",
                                "type": "llm",
                                "kwargs": {"service": svc, "model_id": resolved_model},
                                "file": filename,
                                "line": node.lineno,
                                "scope": self._get_scope(node, tree),
                            })

                    if isinstance(node.func, ast.Name):
                        class_name = node.func.id
                        if class_name in AI_CLASSES:
                            # Extract kwargs
                            kwargs = {}
                            for keyword in node.keywords:
                                if isinstance(keyword.value, ast.Constant):
                                    kwargs[keyword.arg] = keyword.value.value
                                elif isinstance(keyword.value, ast.Name):
                                    kwargs[keyword.arg] = f"<var:{keyword.value.id}>"

                            instantiations.append({
                                "class": class_name,
                                "type": AI_CLASSES[class_name],
                                "kwargs": kwargs,
                                "file": filename,
                                "line": node.lineno,
                                "scope": self._get_scope(node, tree),
                            })
        except SyntaxError as e:
            logger.debug(f"scan_instantiations: SyntaxError in {filename} — {e}")
        return instantiations

    def resolve_constants(self, source_code: str) -> dict[str, str]:
        """Level 3: Resolve module-level constants (including os.getenv()).

        Returns dict mapping variable name to resolved value or "<env:VAR_NAME>".
        """
        constants = {}
        try:
            tree = ast.parse(source_code)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            var_name = target.id
                            if isinstance(node.value, ast.Constant):
                                constants[var_name] = str(node.value.value)
                            elif isinstance(node.value, ast.Call):
                                if isinstance(node.value.func, ast.Attribute):
                                    if (isinstance(node.value.func.value, ast.Name) and
                                        node.value.func.value.id == "os" and
                                        node.value.func.attr == "getenv"):
                                        # os.getenv("VAR")
                                        if node.value.args and isinstance(node.value.args[0], ast.Constant):
                                            env_name = node.value.args[0].value
                                            constants[var_name] = f"<env:{env_name}>"
        except SyntaxError as e:
            logger.debug(f"resolve_constants: SyntaxError — {e}")
        return constants

    def _get_scope(self, call_node: ast.Call, tree: ast.Module) -> str:
        """Find the enclosing function or class for a call node."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                for child in ast.walk(node):
                    if child is call_node:
                        return node.name
        return "module_level"

    def analyze_file(self, filepath: str) -> dict:
        """Analyze single Python file for AI stack.

        Returns dict with imports, instantiations, and constants.
        """
        result = {
            "file": str(filepath),
            "imports": {},
            "instantiations": [],
            "constants": {},
        }
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            result["imports"] = self.scan_imports(source)
            result["constants"] = self.resolve_constants(source)
            result["instantiations"] = self.scan_instantiations(source, str(filepath), constants=result["constants"])
        except Exception as e:
            logger.debug(f"analyze_file({filepath}): {e}")
        return result

    def analyze_repo(self, repo_path: str) -> dict:
        """Analyze all Python files in repository.

        Priority order:
        1. Root level (main.py, app.py, config.py, settings.py)
        2. ai/, llm/, agents/, rag/ directories
        3. Other Python files

        Returns dict with aggregated results.
        """
        repo_path = Path(repo_path)
        results = {
            "total_files_analyzed": 0,
            "providers_detected": set(),
            "llm": {},
            "embeddings": {},
            "vector_stores": [],
            "portable_components": [],
        }

        # Priority file order
        priority_files = []
        other_files = []

        # Scan repository
        for py_file in repo_path.rglob("*.py"):
            if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
                continue

            # Check priority
            if py_file.parent == repo_path and py_file.name in ["main.py", "app.py", "config.py", "settings.py"]:
                priority_files.append(py_file)
            elif any(d in py_file.parts for d in ["ai", "llm", "agents", "rag"]):
                priority_files.append(py_file)
            else:
                other_files.append(py_file)

        # Analyze files in priority order
        for py_file in priority_files + other_files[:50]:  # Limit to 50 files
            file_result = self.analyze_file(py_file)
            results["total_files_analyzed"] += 1

            # Aggregate imports
            for inst in file_result["instantiations"]:
                results["providers_detected"].add(inst["class"])
                if inst["type"] == "llm":
                    model_id = inst["kwargs"].get("model_id", "") or inst["kwargs"].get("model", "")
                    results["llm"][inst["class"]] = {
                        "model_id": model_id,
                        "kwargs": inst["kwargs"],
                        "file": str(inst["file"]),
                        "line": inst["line"],
                    }
                elif inst["type"] == "embeddings":
                    model_id = inst["kwargs"].get("model_id", "") or inst["kwargs"].get("model", "")
                    dims = EMBEDDING_DIMENSIONS.get(model_id)
                    results["embeddings"][inst["class"]] = {
                        "model_id": model_id,
                        "dimension": dims,
                        "reembedding_required": dims is not None,
                        "kwargs": inst["kwargs"],
                        "file": str(inst["file"]),
                        "line": inst["line"],
                    }
                elif inst["type"] == "vector_store":
                    results["vector_stores"].append({
                        "class": inst["class"],
                        "kwargs": inst["kwargs"],
                        "file": str(inst["file"]),
                        "line": inst["line"],
                        "portable": inst["class"] in ["FAISS", "Chroma", "Weaviate"],
                    })

        # Detect portable components from vector stores
        for vs in results["vector_stores"]:
            if vs["class"] in ["FAISS", "Chroma", "Weaviate", "Qdrant"]:
                results["portable_components"].append(vs["class"])

        results["providers_detected"] = list(results["providers_detected"])
        return results

