"""
sa_iac_parsers.py — IaC file parsers for all supported formats.

Parsers:
  _detect_pulumi_iac      — Python + YAML Pulumi resource detection.
  _parse_tf_hcl2          — Terraform HCL via python-hcl2.
  _extract_tf_cross_refs  — Extract inter-resource references from TF AST.
  _parse_tf_regex_fallback — Regex fallback when hcl2 is unavailable.
  _cfn_safe_loader        — Safe YAML loader for CloudFormation templates.
  _parse_cf_yaml          — CloudFormation YAML resource extraction.
  _parse_arm_template     — ARM JSON template resource extraction.
  _parse_helm_chart       — Helm Chart.yaml + values.yaml resource extraction.
  _detect_cdk_pulumi      — Detect CDK / Pulumi from package manifests.
"""
from __future__ import annotations
import ast
import io
import json
import logging
import os
import re
import yaml
from typing import Any

try:
    import hcl2 as _hcl2
except ImportError:
    _hcl2 = None  # type: ignore[assignment]

from services.stack_analyzer.constants import (
    PULUMI_PYTHON_IMPORTS, _PULUMI_PROVIDER_MAP,
)
from services.stack_analyzer.service_classifier import _normalize_service_name, _get_service_type

logger = logging.getLogger("StackAnalyzer")


def _unquote(value):
    """Strip the surrounding quotes python-hcl2 v8+ keeps around labels and scalars.

    hcl2 < v4 returned bare values; v8 returns block labels and string scalars
    wrapped in literal double quotes (e.g. '"db.t3.micro"'). Normalize to the bare
    form so version differences are invisible to the rest of the parser.
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value

def _detect_pulumi_iac(content: str, filename: str) -> list[dict]:
    """P22: Detect Pulumi resources from Python or YAML IaC files.

    Handles:
    - Python: `import pulumi_aws as aws` + `aws.s3.Bucket("name", ...)`
    - Pulumi YAML: resources section with type: "aws:s3/bucket:Bucket"

    Returns list of node dicts compatible with the standard dependency_graph format.
    """
    nodes: list[dict] = []

    if filename.endswith(".py"):
        # Detect via Python AST — look for pulumi imports and class instantiations
        has_pulumi = any(pkg in content for pkg in PULUMI_PYTHON_IMPORTS)
        if not has_pulumi:
            return nodes
        try:
            tree = ast.parse(content)
            # Collect alias mappings: "import pulumi_aws as aws" → {"aws": "aws"}
            provider_aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in PULUMI_PYTHON_IMPORTS:
                            cloud = PULUMI_PYTHON_IMPORTS[alias.name]
                            alias_name = alias.asname or alias.name.replace("pulumi_", "")
                            provider_aliases[alias_name] = cloud
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "") in PULUMI_PYTHON_IMPORTS:
                        cloud = PULUMI_PYTHON_IMPORTS[node.module]
                        for alias in node.names:
                            provider_aliases[alias.asname or alias.name] = cloud
            # Find call expressions like: aws.s3.Bucket("name")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
                        # aws.s3.Bucket  → value=aws.s3, attr=Bucket
                        top = func.value
                        if isinstance(top.value, ast.Name) and top.value.id in provider_aliases:
                            cloud = provider_aliases[top.value.id]
                            service_seg = top.attr.lower()  # "s3", "lambda_", "dynamodb" …
                            service_clean = service_seg.rstrip("_")
                            svc_type = _get_service_type(service_clean)
                            resource_id = f"pulumi-{cloud}-{service_clean}"
                            nodes.append({
                                "id": resource_id,
                                "type": svc_type,
                                "cloud_confirmed": True,
                                "cloud_provider": cloud,
                                "iac_type": "pulumi-python",
                                "score": 5,
                                "complexity": "MEDIUM",
                            })
        except SyntaxError:
            pass

    elif filename in ("Pulumi.yaml", "Pulumi.yml") or filename.endswith(".pulumi.yaml"):
        # Pulumi YAML: resources section
        try:
            data = yaml.safe_load(content) or {}
            for _res_name, res_def in (data.get("resources") or {}).items():
                res_type = str(res_def.get("type", ""))
                # "aws:s3/bucket:Bucket" → provider=aws, service=s3
                parts = res_type.split(":")
                if len(parts) >= 2:
                    provider = _PULUMI_PROVIDER_MAP.get(parts[0].lower(), parts[0].lower())
                    service_path = parts[1].split("/")[0]  # "s3/bucket" → "s3"
                    svc_type = _get_service_type(service_path)
                    nodes.append({
                        "id": f"pulumi-{provider}-{service_path}",
                        "type": svc_type,
                        "cloud_confirmed": True,
                        "cloud_provider": provider,
                        "iac_type": "pulumi-yaml",
                        "score": 5,
                        "complexity": "MEDIUM",
                    })
        except Exception:
            pass

    return nodes


def _parse_tf_hcl2(
    content: str,
    fpath: str,
    var_values: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Parse un fichier Terraform avec python-hcl2.

    var_values: resolved variable values from variables.tf / *.tfvars — used to
    substitute ${var.name} references in instance_class, assume_role_policy, etc.
    """
    iac_resources: list[dict] = []
    cross_refs: list[dict] = []
    _PREFIX_CLOUD = {"aws_": "aws", "azurerm_": "azure", "google_": "gcp"}
    _vars = var_values or {}

    parsed = _hcl2.load(io.StringIO(content))
    for res_block in parsed.get("resource", []):
        if not isinstance(res_block, dict):
            continue
        for res_type, instances in res_block.items():
            res_type = _unquote(res_type)
            cloud = "unknown"
            for prefix, cloud_name in _PREFIX_CLOUD.items():
                if res_type.startswith(prefix):
                    cloud = cloud_name
                    break
            svc = _normalize_service_name(res_type)
            # hcl2 v4+ wraps instances in a list: [{"res_name": config}, ...]
            # hcl2 v3 returns instances as a dict: {"res_name": config}
            if isinstance(instances, list):
                merged: dict = {}
                for item in instances:
                    if isinstance(item, dict):
                        merged.update(item)
                instances = merged
            if not isinstance(instances, dict):
                continue
            for res_name, config in instances.items():
                node: dict = {
                    "service": svc, "cloud": cloud,
                    "file": fpath, "resource_name": _unquote(res_name),
                }
                if isinstance(config, dict):
                    _extract_tf_cross_refs(config, svc, cross_refs, _PREFIX_CLOUD)
                    # IAM trust policy — dynamic LLM resolution with var support
                    if res_type == "aws_iam_role":
                        trust = _extract_iam_trust_service(config, _vars)
                        if trust:
                            node["contextual_hints"] = {"iam_trust_service": trust}
                    # RDS: extract engine + instance_class so Agent 01 maps to the
                    # correct Azure/GCP equivalent (PostgreSQL ≠ MySQL ≠ SQL Server).
                    elif res_type in ("aws_db_instance", "aws_rds_cluster_instance"):
                        raw_class = (
                            _get_str(config, "instance_class")
                            or _get_str(config, "db_instance_class")
                        )
                        instance_class = _resolve_var(raw_class, _vars) or raw_class
                        hints: dict = {}
                        if instance_class and not instance_class.startswith("${"):
                            hints["instance_class"] = instance_class
                        # engine is the most critical attribute for cross-cloud mapping
                        raw_engine = _get_str(config, "engine")
                        engine = _resolve_var(raw_engine, _vars) or raw_engine
                        if engine and not engine.startswith("${"):
                            hints["engine"] = engine.lower()
                        raw_engine_ver = _get_str(config, "engine_version")
                        engine_version = _resolve_var(raw_engine_ver, _vars) or raw_engine_ver
                        if engine_version and not engine_version.startswith("${"):
                            hints["engine_version"] = engine_version
                        if hints:
                            node["contextual_hints"] = hints
                    # S3 → detect versioning / encryption hints
                    elif res_type == "aws_s3_bucket":
                        node["contextual_hints"] = {"resource_type": "aws_s3_bucket"}
                    # Bedrock — any aws_bedrock_* resource proves the app uses a managed
                    # LLM on AWS. Canonical service = "bedrock", category = "ai".
                    # This hint ensures Agent 01 maps to azurerm_cognitive_account on Azure.
                    elif res_type.startswith("aws_bedrock") or res_type.startswith("aws_bedrockagent"):
                        model_id = (
                            _get_str(config, "model_id")
                            or _get_str(config, "base_model_identifier")
                            or _get_str(config, "foundation_model_id")
                            or "unknown"
                        )
                        node["contextual_hints"] = {
                            "ai_service": "bedrock",
                            "model_id": model_id,
                            "category": "ai",
                            "hint": (
                                f"AWS Bedrock resource '{res_type}' — managed LLM inference. "
                                "Migrate to Azure OpenAI Service (azurerm_cognitive_account kind=OpenAI)."
                            ),
                        }
                iac_resources.append(node)

    for mod_block in parsed.get("module", []):
        if isinstance(mod_block, dict):
            for mod_name in mod_block:
                iac_resources.append({
                    "service": f"module-{mod_name}", "cloud": "unknown",
                    "file": fpath, "resource_name": mod_name,
                })
    return iac_resources, cross_refs


def _extract_tf_cross_refs(
    config: dict, source_svc: str,
    cross_refs: list[dict],
    prefix_cloud: dict[str, str],
) -> None:
    """Extrait les références croisées Terraform (interpolations + depends_on).

    Captures the full resource type from Terraform references such as:
      ${aws_s3_bucket.my_bucket.id}      → target "s3"
      ${aws_lambda_function.fn.arn}      → target "lambda"
      depends_on = [aws_iam_role.role]   → target "iam"
    """
    # Match ${resource_type.resource_name…} — captures the full resource type
    # e.g. ${aws_s3_bucket.bucket.id} → group 1 = "aws_s3_bucket"
    ref_pattern = re.compile(r'\$\{(?:data\.)?([a-z][a-z0-9_]+)\.[a-z_]')
    # Match "resource_type.resource_name" in depends_on list entries
    dep_pattern = re.compile(r'^([a-z][a-z0-9_]+)\.')

    def _ref_to_svc(raw: str) -> str:
        """Normalize a raw Terraform resource type to a canonical service name."""
        return _normalize_service_name(raw)

    for key, value in config.items():
        # depends_on list — explicit declarative dependencies
        if key == "depends_on" and isinstance(value, list):
            for dep in value:
                dep_str = str(dep).strip()
                m = dep_pattern.match(dep_str)
                if m:
                    target_svc = _ref_to_svc(m.group(1))
                    if target_svc != source_svc:
                        cross_refs.append({
                            "from": source_svc, "to": target_svc, "relation": "depends_on",
                        })

        # String values — interpolated references ${resource_type.name.attr}
        elif isinstance(value, str):
            for match in ref_pattern.finditer(value):
                target_svc = _ref_to_svc(match.group(1))
                if target_svc != source_svc:
                    cross_refs.append({
                        "from": source_svc, "to": target_svc, "relation": "references",
                    })

        # Nested dict — recurse
        elif isinstance(value, dict):
            _extract_tf_cross_refs(value, source_svc, cross_refs, prefix_cloud)

        # List — recurse into nested dicts and strings
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _extract_tf_cross_refs(item, source_svc, cross_refs, prefix_cloud)
                elif isinstance(item, str):
                    for match in ref_pattern.finditer(item):
                        target_svc = _ref_to_svc(match.group(1))
                        if target_svc != source_svc:
                            cross_refs.append({
                                "from": source_svc, "to": target_svc, "relation": "references",
                            })


def _get_str(config: dict, key: str) -> str | None:
    """Safely extract a string value from a parsed HCL config dict."""
    val = config.get(key)
    if isinstance(val, str):
        return _unquote(val)
    if isinstance(val, list) and val and isinstance(val[0], str):
        return _unquote(val[0])
    return None


def _parse_tf_variables(content: str) -> dict[str, str]:
    """
    Parse a variables.tf file and return {var_name: default_value}.
    Uses python-hcl2 when available, regex fallback otherwise.
    """
    if _hcl2 is not None:
        try:
            parsed = _hcl2.load(io.StringIO(content))
            result: dict[str, str] = {}
            for var_block in parsed.get("variable", []):
                if not isinstance(var_block, dict):
                    continue
                for var_name, var_cfg in var_block.items():
                    if isinstance(var_cfg, list) and var_cfg:
                        var_cfg = var_cfg[0]
                    if isinstance(var_cfg, dict):
                        default = var_cfg.get("default")
                        if isinstance(default, list) and default:
                            default = default[0]
                        if default is not None and isinstance(default, (str, int, float, bool)):
                            result[_unquote(var_name)] = _unquote(str(default))
            return result
        except Exception:
            pass
    # Regex fallback
    result = {}
    for m in re.finditer(
        r'variable\s+"(\w+)"\s*\{[^}]*default\s*=\s*"([^"]+)"', content, re.DOTALL
    ):
        result[m.group(1)] = m.group(2)
    return result


def _parse_tfvars(content: str) -> dict[str, str]:
    """
    Parse a .tfvars file and return {var_name: value}.
    .tfvars is a subset of HCL — python-hcl2 can parse it directly.
    """
    if _hcl2 is not None:
        try:
            parsed = _hcl2.load(io.StringIO(content))
            return {
                _unquote(k): _unquote(str(v[0] if isinstance(v, list) and v else v))
                for k, v in parsed.items()
                if k != "__is_block__"
                and (
                    isinstance(v, (str, int, float, bool))
                    or (isinstance(v, list) and v and isinstance(v[0], (str, int, float, bool)))
                )
            }
        except Exception:
            pass
    # Regex fallback: key = "value" or key = value
    result = {}
    for m in re.finditer(r'^(\w+)\s*=\s*"([^"]+)"', content, re.MULTILINE):
        result[m.group(1)] = m.group(2)
    return result


def _resolve_var(value: str | None, var_values: dict[str, str]) -> str | None:
    """
    Resolve a Terraform variable reference to its actual value.
    Handles: "${var.name}", "${var.name}", "var.name"
    Returns the original value unchanged if not resolvable.
    """
    if not value or not var_values:
        return value
    stripped = value.strip()
    # Full interpolation: "${var.name}"
    m = re.fullmatch(r'\$\{var\.(\w+)\}', stripped)
    if m:
        return var_values.get(m.group(1), value)
    # Plain reference: "var.name"
    m = re.fullmatch(r'var\.(\w+)', stripped)
    if m:
        return var_values.get(m.group(1), value)
    return value


def _resolve_trust_from_policy_dict(policy: dict) -> dict | None:
    """Extract trust principals from a parsed IAM policy dict and call LLM."""
    principals: list[str] = []
    for stmt in policy.get("Statement", []):
        p = stmt.get("Principal", {})
        if isinstance(p, dict):
            svc = p.get("Service", [])
            if isinstance(svc, str):
                svc = [svc]
            principals.extend(svc)
        elif isinstance(p, str) and p != "*":
            principals.append(p)
    if not principals:
        return None
    return _resolve_iam_trust_llm(json.dumps(policy)[:400], principals)


def _resolve_iam_trust_llm(policy_json: str, principals: list[str]) -> dict:
    """
    Use GPT-4o to determine the correct Azure identity resource for an IAM trust policy.
    Falls back to azurerm_user_assigned_identity if LLM call fails.
    """
    try:
        from langchain_openai import AzureChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        from configuration.settings import settings
        import httpx as _httpx, json as _json
        llm = AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_AI_ENDPOINT", ""),
            api_key=os.getenv("AZURE_AI_API_KEY", ""),
            azure_deployment=os.getenv("AZURE_MODEL") or settings.AZURE_MODEL,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION") or settings.AZURE_OPENAI_API_VERSION,
            temperature=0,
            max_tokens=120,
            timeout=_httpx.Timeout(15.0),
        )
        prompt = (
            f"AWS IAM trust principals: {principals}\n"
            f"Policy JSON (truncated): {policy_json[:400]}\n\n"
            "Return ONLY a JSON object (no markdown) with exactly two keys:\n"
            "  azure_target: Terraform resource type string (e.g. azurerm_user_assigned_identity)\n"
            "  hint: one-sentence explanation of the mapping"
        )
        resp = llm.invoke([
            SystemMessage(content=(
                "You are a cloud architect expert in AWS→Azure migration. "
                "Map AWS IAM trust relationships to the correct Azure Terraform resource type. "
                "Most AWS service roles map to azurerm_user_assigned_identity (Managed Identity). "
                "Return only valid JSON, no explanations."
            )),
            HumanMessage(content=prompt),
        ])
        content = (resp.content or "").strip().lstrip("```json").rstrip("```").strip()
        result = _json.loads(content)
        result["trust_principals"] = principals
        return result
    except Exception as exc:
        logger.debug("_resolve_iam_trust_llm(%s): %s", principals, exc)
        return {
            "trust_principals": principals,
            "azure_target": "azurerm_user_assigned_identity",
            "hint": f"IAM role trusted by {principals[0]} → Azure Managed Identity",
        }


def _extract_iam_trust_service(
    config: dict,
    var_values: dict[str, str] | None = None,
) -> dict | None:
    """
    Parse the assume_role_policy of an aws_iam_role resource and use LLM
    to determine the correct Azure identity resource.

    Handles:
    - Plain JSON string
    - jsonencode({...}) Terraform function call
    - Variable references var.* resolved via var_values
    - Dict value returned directly by python-hcl2

    Returns a dict with azure_target, trust_principals, and hint, or None.
    """
    policy_val = config.get("assume_role_policy")

    # Case 1: python-hcl2 returned the jsonencode() content as a Python dict
    if isinstance(policy_val, dict):
        return _resolve_trust_from_policy_dict(policy_val)
    if isinstance(policy_val, list) and policy_val:
        if isinstance(policy_val[0], dict):
            return _resolve_trust_from_policy_dict(policy_val[0])
        if isinstance(policy_val[0], str):
            policy_val = policy_val[0]

    if not isinstance(policy_val, str):
        return None

    policy_raw = policy_val.strip()

    # Case 2: Terraform variable reference — resolve to actual value
    if var_values and (policy_raw.startswith("${var.") or policy_raw.startswith("var.")):
        resolved = _resolve_var(policy_raw, var_values)
        if resolved and resolved != policy_raw:
            policy_raw = resolved

    # Case 3: Plain JSON string
    try:
        import json as _json
        policy = _json.loads(policy_raw)
        return _resolve_trust_from_policy_dict(policy)
    except (ValueError, _json.JSONDecodeError):
        pass

    # Case 4: jsonencode() wrapper or raw HCL — extract amazonaws.com principals via regex
    if "amazonaws.com" in policy_raw or "jsonencode" in policy_raw:
        principals = list({
            m.group(1)
            for m in re.finditer(r'"([a-z0-9.-]+\.amazonaws\.com)"', policy_raw)
        })
        if principals:
            return _resolve_iam_trust_llm(policy_raw[:400], principals)

    return None


def _parse_tf_regex_fallback(content: str, fpath: str) -> list[dict]:
    iac_resources: list[dict] = []
    for match in re.finditer(r'resource\s+"(aws_|azurerm_|google_)(\w+)"', content):
        prefix, name = match.group(1), match.group(2)
        cloud = {"aws_": "aws", "azurerm_": "azure", "google_": "gcp"}[prefix]
        full_res_type = prefix + name
        svc = _normalize_service_name(full_res_type)
        iac_resources.append({"service": svc, "cloud": cloud, "file": fpath})
    return iac_resources



def _cfn_safe_loader():
    """Loader YAML tolérant qui ignore les tags intrinsèques CloudFormation (!Ref, !Sub, !GetAtt, etc.)

    Crée une SOUS-CLASSE de SafeLoader (jamais la classe globale elle-même) pour
    éviter de polluer l'état global de PyYAML dans d'autres parties du code.
    """
    class _CFNLoader(yaml.SafeLoader):
        pass

    _CFNLoader.add_multi_constructor(
        "",
        lambda l, tag, node: (
            l.construct_scalar(node)
            if isinstance(node, yaml.ScalarNode)
            else l.construct_object(node)
        ),
    )
    return _CFNLoader


def _parse_cf_yaml(content: str, fpath: str) -> list[dict]:
    """Parse un fichier CloudFormation (YAML structuré).

    Tolère les tags intrinsèques CFN comme !Ref, !Sub, !GetAtt, !If, etc.
    en les traitant comme des valeurs scalaires.
    """
    _CF_CLOUD_MAP = {"AWS": "aws", "Azure": "azure", "Google": "gcp"}
    iac_resources: list[dict] = []
    try:
        data = yaml.load(content, Loader=_cfn_safe_loader())
        if not isinstance(data, dict):
            return []
        resources_section = data.get("Resources", data.get("resources", {}))
        if not isinstance(resources_section, dict):
            return []
        seen: set[str] = set()
        for _, res_def in resources_section.items():
            if not isinstance(res_def, dict):
                continue
            res_type = res_def.get("Type", "")
            if not res_type:
                continue
            parts = str(res_type).split("::")
            if len(parts) >= 2 and parts[0] in _CF_CLOUD_MAP:
                cloud = _CF_CLOUD_MAP[parts[0]]
                svc = parts[1].lower()
                if svc not in seen:
                    seen.add(svc)
                    iac_resources.append({
                        "service": svc, "cloud": cloud,
                        "file": fpath, "source": "yaml_structured",
                    })
        logger.info(f"_parse_cf_yaml: {len(iac_resources)} ressources dans {fpath}")
    except Exception as exc:
        logger.debug(f"_parse_cf_yaml: échec pour {fpath}: {exc}")
        return []
    return iac_resources


# ARM Templates parser (Azure Resource Manager JSON)
# ─────────────────────────────────────────────────────────────────────────────

_ARM_NAMESPACE_MAP: dict[str, tuple[str, str]] = {
    "microsoft.compute":           ("compute",   "azure"),
    "microsoft.storage":           ("storage",   "azure"),
    "microsoft.web":               ("compute",   "azure"),
    "microsoft.network":           ("network",   "azure"),
    "microsoft.sql":               ("database",  "azure"),
    "microsoft.documentdb":        ("database",  "azure"),
    "microsoft.containerservice":  ("compute",   "azure"),
    "microsoft.keyvault":          ("iam",       "azure"),
    "microsoft.servicebus":        ("messaging", "azure"),
    "microsoft.eventhub":          ("messaging", "azure"),
    "microsoft.insights":          ("monitoring","azure"),
    "microsoft.cognitiveservices": ("ai",        "azure"),
    "microsoft.machinelearning":   ("ai",        "azure"),
    "microsoft.search":            ("search",    "azure"),
    "microsoft.cache":             ("database",  "azure"),
    "microsoft.dbforpostgresql":   ("database",  "azure"),
    "microsoft.dbformysql":        ("database",  "azure"),
    "microsoft.containerregistry": ("compute",   "azure"),
    "microsoft.app":               ("compute",   "azure"),
    "microsoft.apimanagement":     ("network",   "azure"),
    "microsoft.operationalinsights":("monitoring","azure"),
    "microsoft.authorization":     ("iam",       "azure"),
    "microsoft.managedidentity":   ("iam",       "azure"),
}


def _parse_arm_template(content: str, fpath: str) -> list[dict]:
    """Parse Azure ARM template JSON and extract cloud resources.

    ARM templates use:  "type": "Microsoft.Compute/virtualMachines"
    The namespace (first segment before '/') maps directly to service type.
    """
    iac_resources: list[dict] = []
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return []
        resources = data.get("resources", [])
        if not isinstance(resources, list):
            return []
        seen: set[str] = set()
        for res in resources:
            if not isinstance(res, dict):
                continue
            res_type = str(res.get("type", "")).lower()
            if not res_type:
                continue
            namespace = res_type.split("/")[0]
            if namespace not in _ARM_NAMESPACE_MAP:
                logger.warning(
                    "Unknown ARM namespace '%s' in %s — "
                    "resource will be flagged as partial_coverage. "
                    "Known namespaces: %d. Consider adding it to _ARM_NAMESPACE_MAP.",
                    namespace, fpath, len(_ARM_NAMESPACE_MAP),
                )
            svc_type, cloud = _ARM_NAMESPACE_MAP.get(namespace, ("unknown", "azure"))
            # Derive a canonical service name from the resource type
            parts = res_type.split("/")
            svc_name = _normalize_service_name(
                "azurerm_" + "_".join(p.replace("-", "_") for p in parts)
            )
            if svc_name not in seen:
                seen.add(svc_name)
                iac_resources.append({
                    "service": svc_name,
                    "cloud": cloud,
                    "file": fpath,
                    "source": "arm_template",
                    "resource_name": res.get("name", ""),
                    "coverage": "partial" if namespace not in _ARM_NAMESPACE_MAP else "full",
                })
    except (json.JSONDecodeError, Exception) as exc:
        logger.debug(f"_parse_arm_template: failed for {fpath}: {exc}")
    logger.info(f"_parse_arm_template: {len(iac_resources)} resources in {fpath}")
    return iac_resources


# ─────────────────────────────────────────────────────────────────────────────
# Helm Charts parser (values.yaml + Chart.yaml)
# ─────────────────────────────────────────────────────────────────────────────

_HELM_IMAGE_SERVICE_MAP: dict[str, tuple[str, str]] = {
    "postgres":      ("database", "any"),
    "postgresql":    ("database", "any"),
    "mysql":         ("database", "any"),
    "mariadb":       ("database", "any"),
    "redis":         ("database", "any"),
    "mongodb":       ("database", "any"),
    "mongo":         ("database", "any"),
    "rabbitmq":      ("messaging", "any"),
    "kafka":         ("messaging", "any"),
    "elasticsearch": ("search",   "any"),
    "opensearch":    ("search",   "any"),
    "grafana":       ("monitoring","any"),
    "prometheus":    ("monitoring","any"),
    "nginx":         ("network",  "any"),
    "traefik":       ("network",  "any"),
}

_HELM_DEPENDENCY_MAP: dict[str, tuple[str, str]] = {
    "postgresql":   ("postgres",      "database"),
    "mysql":        ("mysql",         "database"),
    "redis":        ("redis",         "database"),
    "mongodb":      ("documentdb",    "database"),
    "rabbitmq":     ("rabbitmq",      "messaging"),
    "kafka":        ("kafka",         "messaging"),
    "elasticsearch":("opensearch",    "search"),
    "grafana":      ("grafana",       "monitoring"),
    "prometheus":   ("prometheus",    "monitoring"),
}


def _parse_helm_chart(values_content: str, chart_content: str, fpath: str) -> list[dict]:
    """Parse Helm Chart.yaml + values.yaml to detect services.

    Looks for:
    - Chart.yaml dependencies (postgresql, redis, kafka…)
    - values.yaml image.repository entries
    - values.yaml explicit service host/url overrides
    """
    iac_resources: list[dict] = []
    seen: set[str] = set()

    def _add(svc: str, svc_type: str = "unknown", cloud: str = "any") -> None:
        if svc not in seen:
            seen.add(svc)
            iac_resources.append({
                "service": svc, "cloud": cloud, "file": fpath,
                "source": "helm_chart", "type": svc_type,
            })

    # Chart.yaml — dependencies block
    try:
        chart = yaml.safe_load(chart_content) or {}
        for dep in chart.get("dependencies", []) or []:
            dep_name = str(dep.get("name", "")).lower()
            if dep_name in _HELM_DEPENDENCY_MAP:
                svc, svc_type = _HELM_DEPENDENCY_MAP[dep_name]
                _add(svc, svc_type)
    except Exception as exc:
        logger.debug(f"_parse_helm_chart Chart.yaml: {exc}")

    # values.yaml — image.repository + service host patterns
    try:
        values = yaml.safe_load(values_content) or {}
        if not isinstance(values, dict):
            values = {}

        def _walk_values(obj: Any, depth: int = 0) -> None:
            if depth > 6 or not isinstance(obj, dict):
                return
            repo = str(obj.get("repository", "")).lower()
            if repo:
                base = repo.split("/")[-1]
                for img_key, (svc_type, cloud) in _HELM_IMAGE_SERVICE_MAP.items():
                    if base.startswith(img_key):
                        _add(base, svc_type, cloud)
                        break
            for v in obj.values():
                if isinstance(v, dict):
                    _walk_values(v, depth + 1)

        _walk_values(values)
    except Exception as exc:
        logger.debug(f"_parse_helm_chart values.yaml: {exc}")

    logger.info(f"_parse_helm_chart: {len(iac_resources)} services from {fpath}")
    return iac_resources


# ─────────────────────────────────────────────────────────────────────────────
# CDK / Pulumi detection (flag only — no LLM call)
# ─────────────────────────────────────────────────────────────────────────────

_CDK_PACKAGES: dict[str, str] = {
    "aws-cdk-lib":      "aws_cdk",
    "aws_cdk":          "aws_cdk",
    "constructs":       "aws_cdk",          # CDK peer dep
    "@aws-cdk/core":    "aws_cdk",
    "cdktf":            "cdktf",
    "pulumi":           "pulumi",
    "pulumi_aws":       "pulumi_aws",
    "pulumi_azure":     "pulumi_azure",
    "pulumi_gcp":       "pulumi_gcp",
    "azure-mgmt-resource": "azure_sdk_mgmt",
}


def _detect_cdk_pulumi(manifest_packages: list[str], source_files: dict[str, str]) -> dict:
    """Detect CDK / Pulumi / Azure SDK Management usage.

    Returns a dict with:
      cdk_detected: bool
      cdk_type: "aws_cdk" | "cdktf" | "pulumi_aws" | "pulumi_azure" | ... | None
      cdk_warning: str — message to inject in graph["warnings"]
    """
    detected_type: str | None = None
    for pkg in manifest_packages:
        pkg_clean = pkg.lower().replace("-", "_")
        for cdk_pkg, cdk_type in _CDK_PACKAGES.items():
            if pkg_clean.startswith(cdk_pkg.replace("-", "_")):
                detected_type = cdk_type
                break
        if detected_type:
            break

    # Fallback: scan import statements in Python files
    if not detected_type:
        for fname, src in (source_files or {}).items():
            if not fname.endswith(".py"):
                continue
            for cdk_pkg, cdk_type in _CDK_PACKAGES.items():
                if f"import {cdk_pkg.replace('-', '_')}" in src or f"from {cdk_pkg.replace('-', '_')}" in src:
                    detected_type = cdk_type
                    break
            if detected_type:
                break

    if not detected_type:
        return {"cdk_detected": False, "cdk_type": None, "cdk_warning": None}

    warning = (
        f"Format Pulumi/CDK détecté ({detected_type}) — sélection manuelle requise. "
        "Formats supportés par l'analyse statique : Terraform, Bicep, ARM, Helm, CloudFormation. "
        "IaC généré à l'exécution : les fichiers .tf statiques sont absents. "
        "Action recommandée : exécuter `cdk synth` ou `pulumi preview --json`, "
        "puis fournir la sortie Terraform générée pour une analyse complète."
    )
    return {"cdk_detected": True, "cdk_type": detected_type, "cdk_warning": warning}


