"""
iac_validator.py — Local IaC validation (terraform + checkov + infracost).

Replaces the MCP IaC container (port 8001). Tools are called directly
via subprocess — no network, no separate Docker container.

Terraform, Checkov and Infracost must be installed in the same environment
as the application (see Dockerfile). If a tool is missing, the function returns
an explicit error without crashing the import.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

logger = logging.getLogger("IaCValidator")

# ── Patterns HCL ─────────────────────────────────────────────────────────────

_FILE_MARKER = re.compile(
    r"^#\s*[─\-]+\s*([\w.\-]+\.tf)\s*[─\-]*\s*$",
    re.MULTILINE,
)
_TOP_BLOCK = re.compile(
    r'^(terraform\s*\{|provider\s+"[^"]+"\s*\{)',
    re.MULTILINE,
)
_RES_BLOCK_START = re.compile(
    r'^[ \t]*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# ── Helpers workspace ─────────────────────────────────────────────────────────

def _tf_cache() -> str:
    cache = os.getenv("TF_PLUGIN_CACHE_DIR", os.path.join(tempfile.gettempdir(), "tf-plugins"))
    os.makedirs(cache, exist_ok=True)
    return cache


def _split_providers(content: str) -> tuple[str, str]:
    provider_parts: list[str] = []
    resource_parts: list[str] = []
    i, n = 0, len(content)
    while i < n:
        m = _TOP_BLOCK.search(content, i)
        if not m:
            resource_parts.append(content[i:])
            break
        if m.start() > i:
            resource_parts.append(content[i:m.start()])
        start, depth, j = m.start(), 0, m.start()
        while j < n:
            if content[j] == "{":
                depth += 1
            elif content[j] == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        provider_parts.append(content[start:j])
        i = j
    return "\n".join(provider_parts).strip(), "\n".join(resource_parts).strip()


def _dedup_resources(content: str, seen: set) -> str:
    result: list[str] = []
    pos, n = 0, len(content)
    while pos < n:
        m = _RES_BLOCK_START.search(content, pos)
        if not m:
            result.append(content[pos:])
            break
        rtype, rname = m.group(1), m.group(2)
        result.append(content[pos:m.start()])
        depth, j, in_str = 0, m.start(), False
        while j < n:
            c = content[j]
            if in_str:
                if c == "\\" and j + 1 < n:
                    j += 2
                    continue
                if c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
            j += 1
        key = (rtype, rname)
        if key not in seen:
            seen.add(key)
            result.append(content[m.start():j])
        pos = j
    return "".join(result)


def _sanitize_hcl(content: str) -> str:
    """Replace literal \\n escape sequences with real newlines.

    The LLM occasionally emits HCL with literal backslash-n instead of
    real newline characters, which causes `terraform init` to fail with
    'Error: Invalid character'. This normalises the content before writing.
    """
    # Replace literal \n (two chars: backslash + n) with real newline
    content = content.replace("\\n", "\n")
    # Replace literal \t with real tab
    content = content.replace("\\t", "\t")
    return content


def _write_tf_workspace(terraform_code: str) -> str:
    """Writes Terraform code into a temporary directory.

    Recognizes file markers produced by agent_02:
        # ─── database.tf ───
    Otherwise writes everything into main.tf.
    """
    terraform_code = _sanitize_hcl(terraform_code)
    tmp = tempfile.mkdtemp(prefix="cloud-migrator-tf-")
    parts = _FILE_MARKER.split(terraform_code)

    if len(parts) >= 3:
        combined_providers: list[str] = []
        seen_terraform_block = False
        seen_resources: set[tuple[str, str]] = set()

        for i in range(1, len(parts) - 1, 2):
            filename = parts[i].strip()
            content  = parts[i + 1].strip()
            if not filename.endswith(".tf") or not content:
                continue
            provider_blocks, resource_blocks = _split_providers(content)
            if provider_blocks and not seen_terraform_block:
                combined_providers.append(provider_blocks)
                seen_terraform_block = True
            if resource_blocks:
                resource_blocks = _dedup_resources(resource_blocks, seen_resources)
            if resource_blocks.strip():
                with open(os.path.join(tmp, filename), "w", encoding="utf-8") as f:
                    f.write(resource_blocks + "\n")

        if combined_providers:
            with open(os.path.join(tmp, "_providers.tf"), "w", encoding="utf-8") as f:
                f.write("\n\n".join(combined_providers) + "\n")
    else:
        with open(os.path.join(tmp, "main.tf"), "w", encoding="utf-8") as f:
            f.write(terraform_code)

    return tmp


# ── terraform init avec retry ─────────────────────────────────────────────────

_NETWORK_PATTERNS = (
    "no such host", "lookup registry.terraform.io", "connection refused",
    "network is unreachable", "dial tcp", "i/o timeout", "certificate",
    "tls handshake", "context deadline exceeded", "temporary failure in name resolution",
)


def _is_network_error(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _NETWORK_PATTERNS)


def _terraform_init(tmp_dir: str, retries: int = 3, backoff: float = 5.0) -> dict[str, Any]:
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": _tf_cache()}
    cmd = ["terraform", "init", "-backend=false", "-no-color", "-input=false"]
    last_error, last_type = "", "unknown"

    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                cmd, cwd=tmp_dir, capture_output=True, text=True, env=env, timeout=120,
            )
            if result.returncode == 0:
                return {"ok": True, "error": "", "error_type": "none", "hint": ""}

            err = (result.stderr or result.stdout or "empty output")[:1000]
            last_error = err

            if _is_network_error(err):
                last_type = "network"
                if attempt < retries:
                    time.sleep(backoff * attempt)
                    continue
                return {
                    "ok": False, "error": err, "error_type": "network",
                    "hint": (
                        "Provider registry unreachable. "
                        "Set TF_PLUGIN_CACHE_DIR and pre-populate the cache, "
                        "or check network/proxy settings."
                    ),
                }
            last_type = "config"
            return {"ok": False, "error": err, "error_type": "config", "hint": ""}

        except subprocess.TimeoutExpired:
            last_error, last_type = "terraform init timeout (120s)", "timeout"
            if attempt < retries:
                time.sleep(backoff * attempt)

    return {"ok": False, "error": last_error, "error_type": last_type, "hint": ""}


# ── API publique ──────────────────────────────────────────────────────────────

def terraform_validate(terraform_code: str) -> dict[str, Any]:
    """Validates HCL syntax + schema via `terraform validate -json`.

    Returns:
        {valid: bool, diagnostics: list[dict], error?: str}
    """
    if not shutil.which("terraform"):
        return {"valid": False, "diagnostics": [],
                "error": "terraform not available — install Terraform >= 1.5"}

    tmp_dir = None
    try:
        tmp_dir = _write_tf_workspace(terraform_code)
        init = _terraform_init(tmp_dir)
        if not init["ok"]:
            return {
                "valid": False, "diagnostics": [],
                "error": f"terraform init: {init['error']}",
                "error_type": init.get("error_type", "unknown"),
                "hint": init.get("hint", ""),
            }

        # Inject placeholder ARM env vars so azurerm v4+ provider schema validation
        # does not fail with "subscription_id is a required" during offline HCL checks.
        # These are NOT real credentials — terraform validate never contacts Azure.
        # Note: Azure rejects the all-zeros UUID (AADSTS700038) as invalid application
        # identifier even for offline schema checks. Use a non-zero placeholder UUID.
        _validate_env = {
            **os.environ,
            "ARM_SUBSCRIPTION_ID": os.environ.get("ARM_SUBSCRIPTION_ID", "11111111-1111-1111-1111-111111111111"),
            "ARM_TENANT_ID":       os.environ.get("ARM_TENANT_ID",       "11111111-1111-1111-1111-111111111111"),
            "ARM_CLIENT_ID":       os.environ.get("ARM_CLIENT_ID",       "11111111-1111-1111-1111-111111111111"),
            "ARM_CLIENT_SECRET":   os.environ.get("ARM_CLIENT_SECRET",   "placeholder-for-validate-only"),
        }
        proc = subprocess.run(
            ["terraform", "validate", "-json", "-no-color"],
            cwd=tmp_dir, capture_output=True, text=True, timeout=60,
            env=_validate_env,
        )
        if proc.stdout:
            try:
                payload = json.loads(proc.stdout)
                diags = payload.get("diagnostics", [])
                logger.info(
                    "terraform_validate: valid=%s, %d diagnostics",
                    payload.get("valid"), len(diags),
                )
                for d in diags:
                    loc = ""
                    r = d.get("range") or {}
                    if r.get("filename"):
                        loc = f" [{r['filename']}:{(r.get('start') or {}).get('line','?')}]"
                    logger.warning(
                        "terraform_validate diagnostic%s: %s — %s",
                        loc, d.get("summary", ""), d.get("detail", "")[:200],
                    )
                return {"valid": payload.get("valid", False),
                        "diagnostics": diags}
            except json.JSONDecodeError:
                pass

        stderr = (proc.stderr or proc.stdout or "empty output")[:500]
        return {"valid": False, "diagnostics": [], "error": stderr}

    except subprocess.TimeoutExpired:
        return {"valid": False, "diagnostics": [], "error": "Timeout terraform validate (60s)"}
    except Exception as exc:
        logger.error("terraform_validate: %s", exc)
        return {"valid": False, "diagnostics": [], "error": str(exc)}
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_dryrun_provider_override(tmp_dir: str) -> None:
    """Write a provider_override.tf that disables ALL azurerm authentication.

    azurerm v4+ contacts Azure to acquire an OAuth token even with
    -refresh=false, because the provider plugin itself calls
    clientCredentialsToken during Configure(). The only way to suppress
    that network call in a fully offline dry-run is to override every
    auth method to false and inject a valid-format (but fake) client_secret.

    Terraform's override mechanism (files ending in _override.tf) merges
    blocks rather than replacing them, so only the auth-related attributes
    are touched — resource blocks in the main files are unchanged.
    """
    override_hcl = '''
# provider_override.tf — generated by cloud-migrator for offline dry-run only.
# Disables all azurerm auth paths so terraform plan -refresh=false never
# contacts login.microsoftonline.com. Real credentials are injected by the
# TerraformRunner (executor) at apply time via Vault.
provider "azurerm" {
  features {}
  skip_provider_registration = true
  use_cli                    = false
  use_msi                    = false
  use_oidc                   = false
  # client_secret must be non-empty to satisfy the v4 validation schema,
  # but it is never sent (use_cli/use_msi/use_oidc all false and the plan
  # exits before any token exchange occurs with -refresh=false).
  client_secret = "dryrun-placeholder-not-used"
}
'''
    override_path = os.path.join(tmp_dir, "provider_override.tf")
    with open(override_path, "w", encoding="utf-8") as f:
        f.write(override_hcl)


def terraform_plan_dryrun(
    terraform_code: str,
    provider_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Dry-runs `terraform plan -refresh=false` without real cloud credentials.

    azurerm v4+ contacts Azure even with -refresh=false (it authenticates during
    provider Configure()). We neutralise this by writing a provider_override.tf
    that disables every auth path (use_cli/use_msi/use_oidc=false,
    skip_provider_registration=true). The plan then runs fully offline — only HCL
    schema and cross-reference errors are caught, which is exactly what we need.

    Returns:
        {planned, plan_output, resources_to_add, resources_to_change,
         resources_to_destroy, exit_code, error?}
    """
    if not shutil.which("terraform"):
        return {
            "planned": False, "plan_output": "", "resources_to_add": 0,
            "resources_to_change": 0, "resources_to_destroy": 0, "exit_code": -1,
            "error": "terraform not available",
        }

    tmp_dir = None
    try:
        tmp_dir = _write_tf_workspace(terraform_code)

        # Inject the auth-bypass override so azurerm v4 never tries to acquire
        # a token during plan. Env vars still need valid-format UUIDs to satisfy
        # the provider's schema validation (not the auth layer).
        _write_dryrun_provider_override(tmp_dir)

        env = {
            **os.environ,
            "ARM_SUBSCRIPTION_ID": os.environ.get("ARM_SUBSCRIPTION_ID", "11111111-1111-1111-1111-111111111111"),
            "ARM_TENANT_ID":       os.environ.get("ARM_TENANT_ID",       "11111111-1111-1111-1111-111111111111"),
            "ARM_CLIENT_ID":       os.environ.get("ARM_CLIENT_ID",       "11111111-1111-1111-1111-111111111111"),
            "ARM_CLIENT_SECRET":   os.environ.get("ARM_CLIENT_SECRET",   "dryrun-placeholder-not-used"),
            # Disable any residual auth paths at the env level too
            "ARM_USE_CLI":         "false",
            "ARM_USE_MSI":         "false",
            "ARM_USE_OIDC":        "false",
            **(provider_vars or {}),
        }

        init = _terraform_init(tmp_dir)
        if not init.get("ok"):
            return {
                "planned": False, "plan_output": "", "resources_to_add": 0,
                "resources_to_change": 0, "resources_to_destroy": 0, "exit_code": 1,
                "error": f"terraform init failed: {init.get('error', '')}",
            }

        proc = subprocess.run(
            ["terraform", "plan", "-detailed-exitcode", "-no-color",
             "-input=false", "-refresh=false"],
            cwd=tmp_dir, capture_output=True, text=True, timeout=120, env=env,
        )

        exit_code   = proc.returncode
        plan_output = (proc.stdout or "") + (proc.stderr or "")
        add = change = destroy = 0
        for line in plan_output.splitlines():
            ll = line.lower()
            if "plan:" in ll and "to add" in ll:
                if m := re.search(r"(\d+) to add",     line): add     = int(m.group(1))
                if m := re.search(r"(\d+) to change",  line): change  = int(m.group(1))
                if m := re.search(r"(\d+) to destroy", line): destroy = int(m.group(1))
                break

        result: dict[str, Any] = {
            "planned":              exit_code in (0, 2),
            "plan_output":          plan_output[:4000],
            "resources_to_add":     add,
            "resources_to_change":  change,
            "resources_to_destroy": destroy,
            "exit_code":            exit_code,
        }
        if exit_code == 1:
            result["error"] = (proc.stderr or plan_output)[:500]
        logger.info(
            "terraform_plan_dryrun: exit=%d add=%d change=%d destroy=%d",
            exit_code, add, change, destroy,
        )
        return result

    except subprocess.TimeoutExpired:
        return {
            "planned": False, "plan_output": "", "resources_to_add": 0,
            "resources_to_change": 0, "resources_to_destroy": 0, "exit_code": -1,
            "error": "Timeout terraform plan (120s)",
        }
    except Exception as exc:
        logger.error("terraform_plan_dryrun: %s", exc)
        return {
            "planned": False, "plan_output": "", "resources_to_add": 0,
            "resources_to_change": 0, "resources_to_destroy": 0, "exit_code": -1,
            "error": str(exc),
        }
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# Checkov checks that require enterprise Azure infrastructure the generator cannot
# emit automatically (private endpoints need a VNet, CMK needs Key Vault, queue
# logging in azurerm v4 requires a separate azurerm_storage_account_queue_properties
# resource that cannot be injected without a storage account reference).
# Operators must address these post-migration; they are documented in README.md.
_CHECKOV_SKIP_CHECKS = [
    "CKV2_AZURE_33",  # storage private endpoint (requires VNet/subnet)
    "CKV2_AZURE_57",  # PostgreSQL flexible server private endpoint (requires VNet)
    "CKV2_AZURE_1",   # customer-managed key encryption (requires Azure Key Vault)
    "CKV_AZURE_33",   # queue service logging (azurerm v4: separate resource needed)
    "CKV2_AZURE_21",  # blob logging on storage_container — checkov can't trace the
                      # account reference from the container; logging is set on the
                      # account's blob_properties block (CKV2_AZURE_38 path)
    "CKV2_AZURE_31",  # NSG flow log retention > 90 days — requires azurerm_network_watcher_flow_log
                      # + separate Storage Account + Network Watcher; cannot be auto-injected.
                      # Operators must configure NSG flow logs post-deployment.
    # CKV_AZURE_23, CKV_AZURE_24, CKV2_AZURE_45, CKV2_AZURE_2 — SQL auditing/vulnerability
    # assessment — these are now handled by _CHECKOV_FIX_PATTERNS in iac_fixers.py:
    # the LLM receives the exact Terraform block to write (azurerm_mssql_server_extended_
    # auditing_policy + azurerm_mssql_server_vulnerability_assessment) instead of being
    # skipped. Do NOT add them here — they must be enforced and auto-fixed.
]


def checkov_scan(terraform_code: str) -> dict[str, Any]:
    """Runs a Checkov security scan on Terraform code.

    Returns:
        {passed, failed, skipped, failed_checks: list[dict], error?}
    """
    if not shutil.which("checkov"):
        return {"passed": 0, "failed": 0, "skipped": 0, "failed_checks": [],
                "error": "checkov not available — pip install checkov"}

    tmp_dir = None
    try:
        tmp_dir = _write_tf_workspace(terraform_code)
        proc = subprocess.run(
            [
                "checkov", "-d", tmp_dir, "-o", "json", "--quiet", "--compact",
                "--skip-check", ",".join(_CHECKOV_SKIP_CHECKS),
            ],
            capture_output=True, text=True, timeout=120,
        )

        output = proc.stdout.strip()
        if not output:
            return {"passed": 0, "failed": 0, "skipped": 0, "failed_checks": []}

        raw     = json.loads(output)
        payload = raw[0] if isinstance(raw, list) else raw
        summary = payload.get("summary", {})

        failed_checks = []
        for chk in payload.get("results", {}).get("failed_checks", []):
            failed_checks.append({
                "check_id":       chk.get("check_id", ""),
                "check_name":     chk.get("check_name", ""),
                "resource":       chk.get("resource", ""),
                "file":           os.path.basename(
                    chk.get("repo_file_path") or chk.get("file_path", "")
                ),
                "severity":       chk.get("severity") or "UNKNOWN",
                "guideline":      chk.get("guideline", ""),
                "evaluated_keys": chk.get("check_result", {}).get("evaluated_keys", []),
            })

        result = {
            "passed":        summary.get("passed", 0),
            "failed":        summary.get("failed", 0),
            "skipped":       summary.get("skipped", 0),
            "failed_checks": failed_checks,
        }
        logger.info("checkov_scan: passed=%d failed=%d", result["passed"], result["failed"])
        return result

    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "skipped": 0, "failed_checks": [],
                "error": "Timeout checkov (120s)"}
    except Exception as exc:
        logger.error("checkov_scan: %s", exc)
        return {"passed": 0, "failed": 0, "skipped": 0, "failed_checks": [],
                "error": str(exc)}
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def infracost_estimate(terraform_code: str) -> dict[str, Any]:
    """Estimates monthly cost via infracost breakdown.

    Returns:
        {monthly_cost, currency, resources: list[dict], error?}
    """
    if not shutil.which("infracost"):
        return {"monthly_cost": 0.0, "currency": "USD", "resources": [],
                "error": "infracost not available — see https://www.infracost.io/docs/"}

    tmp_dir = None
    try:
        tmp_dir = _write_tf_workspace(terraform_code)
        _terraform_init(tmp_dir)

        proc = subprocess.run(
            ["infracost", "breakdown", "--path", tmp_dir, "--format", "json", "--no-color"],
            capture_output=True, text=True, timeout=180,
        )

        if proc.stdout:
            data     = json.loads(proc.stdout)
            monthly  = float(data.get("totalMonthlyCost", 0.0) or 0.0)
            currency = data.get("currency", "USD")
            resources = [
                {
                    "name":         res.get("name", ""),
                    "monthly_cost": float(res.get("monthlyCost", 0.0) or 0.0),
                    "unit":         res.get("unit", ""),
                }
                for proj in data.get("projects", [])
                for res  in proj.get("breakdown", {}).get("resources", [])
            ]
            logger.info("infracost_estimate: $%.2f/month (%d resources)", monthly, len(resources))
            return {"monthly_cost": round(monthly, 4), "currency": currency, "resources": resources}

        return {"monthly_cost": 0.0, "currency": "USD", "resources": [],
                "error": (proc.stderr or "No Infracost output")[:300]}

    except subprocess.TimeoutExpired:
        return {"monthly_cost": 0.0, "currency": "USD", "resources": [],
                "error": "Infracost timeout (180s)"}
    except Exception as exc:
        logger.error("infracost_estimate: %s", exc)
        return {"monthly_cost": 0.0, "currency": "USD", "resources": [], "error": str(exc)}
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def check_tools_availability() -> dict[str, Any]:
    """Checks which IaC tools are available in the environment."""
    tf_ok = shutil.which("terraform") is not None
    ck_ok = shutil.which("checkov")   is not None
    ic_ok = shutil.which("infracost") is not None
    return {
        "terraform":     tf_ok,
        "checkov":       ck_ok,
        "infracost":     ic_ok,
        "cache_dir":     _tf_cache(),
        "all_available": tf_ok and ck_ok and ic_ok,
    }
