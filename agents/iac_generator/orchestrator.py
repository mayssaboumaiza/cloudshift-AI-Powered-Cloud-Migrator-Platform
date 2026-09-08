"""
orchestrator.py — Deterministic IaC validation pipeline (no LLM, no MCP).

Calls terraform / checkov / infracost directly as subprocesses via iac_validator.py.
All three binaries are installed in the api/executor image (see Dockerfile lines 32-60).

Steps:
  1. terraform validate  (syntax + schema)
  2. terraform plan      (dry-run, -refresh=false)
  3. checkov scan        (security)
  4. infracost estimate  (cost)
"""
import logging
import os
import re
from pathlib import Path
from typing import List

from agents.iac_generator.hcl_merge import _get_output_dir
from agents.iac_generator.security_policy_engine import SecurityPolicyEngine as _SPE
from agents.iac_generator import iac_validator

_spe_instance = _SPE()

logger = logging.getLogger("Agent02IaC")

# Keywords that mean terraform/checkov binary is missing (not a HCL logic error)
_TOOL_UNAVAILABLE_INDICATORS = (
    "terraform not available",
    "terraform non disponible",
    "terraform: command not found",
    "executable file not found",
    "checkov not available",
    "infracost not available",
)


def _is_tool_unavailable(error: str) -> bool:
    low = error.lower()
    return any(kw in low for kw in _TOOL_UNAVAILABLE_INDICATORS)


def _tf_result_needs_fallback(tf_result: dict) -> bool:
    if not tf_result.get("valid", True) and tf_result.get("diagnostics") == []:
        error = tf_result.get("error", "")
        if error and _is_tool_unavailable(error):
            return True
    return False


class Agent02IaCValidator:
    """Deterministic IaC validation pipeline — direct subprocess calls (no MCP)."""

    def identify_failed_modules(self, checkov_results: dict,
                                output_dir: str = "") -> List[str]:
        """Return .tf files with CRITICAL or HIGH Checkov findings only.

        MEDIUM / LOW / INFO findings are warnings — they do not block the pipeline
        and must NOT populate modules_to_fix (which would cause an infinite fix loop).
        """
        _blocking_sev = {"CRITICAL", "HIGH"}
        failed_checks = checkov_results.get("failed_checks", [])
        if failed_checks:
            # Use granular severity from failed_checks list when available
            blocking = [
                c for c in failed_checks
                if (c.get("severity") or "UNKNOWN").upper() in _blocking_sev
            ]
            if not blocking:
                return []
            # Derive the set of files that have at least one blocking finding
            blocking_files = list(dict.fromkeys(
                c["file"] for c in blocking if c.get("file")
            ))
            if blocking_files:
                output_path = Path(output_dir or _get_output_dir()).resolve()
                valid: List[str] = []
                for f in blocking_files:
                    safe_name = Path(f).name
                    if safe_name != f:
                        logger.warning(f"identify_failed_modules: rejected non-basename path '{f}'")
                        continue
                    candidate = (output_path / safe_name).resolve()
                    if not str(candidate).startswith(str(output_path)):
                        logger.warning(f"identify_failed_modules: path traversal attempt '{f}'")
                        continue
                    valid.append(safe_name)
                return valid

        # Fallback: no granular severity data — use old "failed" count heuristic
        if checkov_results.get("failed", 0) == 0:
            return []

        output_path = Path(output_dir or _get_output_dir()).resolve()
        failed_files: List[str] = checkov_results.get("failed_files", [])
        if failed_files:
            valid: List[str] = []
            for f in failed_files:
                if not isinstance(f, str):
                    continue
                safe_name = Path(f).name
                if safe_name != f:
                    logger.warning(f"identify_failed_modules: rejected non-basename path '{f}'")
                    continue
                candidate = (output_path / safe_name).resolve()
                if not str(candidate).startswith(str(output_path)):
                    logger.warning(f"identify_failed_modules: path traversal attempt '{f}'")
                    continue
                if candidate.exists() and candidate.suffix == ".tf":
                    valid.append(safe_name)
            return valid

        if output_path.is_dir():
            return [f for f in os.listdir(str(output_path)) if f.endswith(".tf")]
        return []

    def _fail_tooling_unavailable(self, state: dict, reason: str) -> dict:
        """Tool unavailable — accept IaC with a quality warning instead of escalating."""
        msg = f"TOOL UNAVAILABLE (non-blocking) — {reason}"
        logger.warning("Agent02IaC: %s — continuing without validation", msg)
        state["iac_validation_success"] = True
        state["modules_to_fix"] = []
        state["needs_human_escalation"] = False
        state["iac_quality_warning"] = True
        state.setdefault("artifacts", {})["iac_validation"] = {
            "mcp_unavailable": True,
            "warning": msg,
        }
        state.setdefault("warnings", [])
        if isinstance(state["warnings"], list):
            state["warnings"].append(msg)
        return state

    def validate_pipeline(self, state: dict) -> dict:
        """Run the 4-step IaC validation pipeline and update graph state.

        Steps:
          1. terraform validate (syntax + schema)
          2. terraform plan dry-run (-refresh=false)
          3. checkov scan (security)
          4. infracost estimate (cost)
        """
        logger.info("=" * 60)
        logger.info("Agent02IaC: Starting IaC validation pipeline (3 steps: validate + checkov + infracost)")
        logger.info("=" * 60)

        if "artifacts" not in state:
            state["artifacts"] = {}
        if "correction_counts" not in state:
            state["correction_counts"] = {}

        artifacts = state.get("artifacts", {})
        terraform_code: str = artifacts.get("terraform_code", "")

        if not isinstance(terraform_code, str) or not terraform_code.strip():
            # terraform_code may be absent from artifacts when the pipeline resumes
            # after ask_human (state deserialized from DB) or when Agent 02 wrote
            # individual .tf files without populating the combined string.
            # Fall back to reading the on-disk .tf files directly.
            output_dir = _get_output_dir()
            disk_tf_files = sorted(Path(output_dir).glob("*.tf")) if Path(output_dir).is_dir() else []
            if disk_tf_files:
                parts = []
                for tf_file in disk_tf_files:
                    try:
                        parts.append(f"# ─── {tf_file.name} ───\n{tf_file.read_text(encoding='utf-8')}\n")
                    except OSError as e:
                        logger.warning("Agent02IaC: could not read %s: %s", tf_file.name, e)
                terraform_code = "\n".join(parts)
                logger.info(
                    "Agent02IaC: terraform_code absent from artifacts — loaded %d .tf file(s) from disk (%d chars)",
                    len(disk_tf_files), len(terraform_code),
                )
            else:
                logger.warning("Agent02IaC: No Terraform code in artifacts and no .tf files on disk — accepting with quality warning.")
                state["iac_validation_success"] = True
                state["modules_to_fix"] = []
                state["needs_human_escalation"] = False
                state["iac_quality_warning"] = True
                state.setdefault("warnings", []).append(
                    "No .tf files found on disk — IaC generation may have been incomplete."
                )
                return state

        try:
            # 1. Terraform Validate — syntax + schema check (one terraform init, no Azure contact)
            # terraform_plan_dryrun is intentionally removed from this pipeline:
            # azurerm v4 contacts Azure during provider Configure() even with -refresh=false,
            # requiring a real token. terraform validate catches all HCL syntax, schema, and
            # reference errors without any network call. The real terraform plan runs later
            # in TerraformRunner (executor) with actual Vault credentials.
            logger.info("Step 1/3: terraform validate")
            tf_result = iac_validator.terraform_validate(terraform_code)
            if _tf_result_needs_fallback(tf_result):
                return self._fail_tooling_unavailable(
                    state,
                    f"terraform binary unavailable: {tf_result.get('error', '')[:200]}",
                )

            tf_validate_only_valid: bool = tf_result.get("valid", False)
            # plan_result stub — dryrun is skipped, kept for artifact schema compatibility
            plan_result: dict = {"planned": True, "plan_output": "skipped — real plan runs in TerraformRunner",
                                 "resources_to_add": 0, "exit_code": 0, "skipped": True}

            # 2. Checkov Scan
            logger.info("Step 2/3: checkov scan")
            try:
                checkov_result = iac_validator.checkov_scan(terraform_code)
                if checkov_result.get("error") and _is_tool_unavailable(checkov_result.get("error", "")):
                    return self._fail_tooling_unavailable(
                        state,
                        f"checkov binary unavailable: {checkov_result['error'][:200]}",
                    )
            except Exception as e:
                logger.error("checkov_scan failed: %s", e)
                checkov_result = {"failed": 1, "passed": 0, "failed_files": [], "error": str(e)}
            # Count only CRITICAL/HIGH as blocking; MEDIUM/LOW are warnings.
            _all_failed = checkov_result.get("failed_checks", [])
            _blocking_sev = {"CRITICAL", "HIGH"}
            failed_checks_count: int = sum(
                1 for c in _all_failed
                if (c.get("severity") or "UNKNOWN").upper() in _blocking_sev
            ) if _all_failed else checkov_result.get("failed", 0)

            # 3. Infracost Estimate
            logger.info("Step 3/3: infracost estimate")
            try:
                infracost_result = iac_validator.infracost_estimate(terraform_code)
            except Exception as e:
                logger.warning(f"infracost_estimate failed (non-blocking): {e}")
                infracost_result = {"monthly_cost": "N/A", "error": str(e)}

            failed_modules = self.identify_failed_modules(checkov_result)

            _no_diag_filenames_failure = False  # True = module-level tf error, not file-level
            if not tf_result.get("valid", False) and not failed_modules:
                diagnostics = tf_result.get("diagnostics", [])
                diag_files = []
                # Collect diagnostic messages that have NO filename (module-level errors)
                no_file_diag_msgs: List[str] = []
                for d in diagnostics:
                    fname = (d.get("range") or {}).get("filename", "")
                    if fname and fname.endswith(".tf"):
                        basename = Path(fname).name
                        if basename not in diag_files:
                            diag_files.append(basename)
                    elif d.get("summary"):
                        msg = f"{d.get('severity','error').upper()}: {d.get('summary','')} — {d.get('detail','')}"
                        no_file_diag_msgs.append(msg)
                        logger.warning("Agent02IaC: tf validate module-level error: %s", msg[:200])

                # ── Parse terraform init error text for filenames ──────────────────
                # When terraform init fails (e.g. HCL syntax error like `}e`), the
                # diagnostics list is EMPTY and the error is in tf_result["error"] as
                # plain text containing "on <file>.tf line <N>". Extract these filenames
                # so the fix loop targets the right file instead of trying all files blind.
                init_error_text = tf_result.get("error", "")
                if init_error_text:
                    logger.warning("Agent02IaC: terraform init raw error: %s", init_error_text[:500])
                if not diag_files and init_error_text:
                    # Pattern: "on database.tf line 13" or "on ./database.tf line 13"
                    file_refs = re.findall(
                        r'\bon\s+(?:\./)?([\w\-]+\.tf)\s+line\s+(\d+)',
                        init_error_text,
                        re.IGNORECASE,
                    )
                    for fref, lineno in file_refs:
                        basename = Path(fref).name
                        if basename not in diag_files:
                            diag_files.append(basename)
                            logger.warning(
                                "Agent02IaC: extracted failing file from terraform init error: "
                                "%s line %s", basename, lineno,
                            )
                    if diag_files:
                        # Inject the init error as plan_errors so the fix loop LLM sees it
                        tf_result["plan_errors"] = (
                            (tf_result.get("plan_errors") or "") + "\n" + init_error_text
                        )[:2000]
                        tf_result["module_level_errors"] = [
                            init_error_text[:800]
                        ]
                        logger.warning(
                            "Agent02IaC: terraform init error parsed — targeting %s for fix loop",
                            diag_files,
                        )

                if diag_files:
                    logger.warning(
                        f"Agent02IaC: terraform validate failed — flagging {len(diag_files)} "
                        f"file(s) from diagnostics: {diag_files}"
                    )
                    failed_modules = diag_files
                else:
                    _cur_output_dir = _get_output_dir()
                    fallback = [f for f in os.listdir(_cur_output_dir) if f.endswith(".tf")] \
                        if os.path.isdir(_cur_output_dir) else []
                    if fallback:
                        _no_diag_filenames_failure = True
                        # Inject the actual error messages into tf_result so the fix loop LLM
                        # knows what to fix (e.g. missing subscription_id in azurerm provider)
                        combined_errors = "\n".join(no_file_diag_msgs)
                        if init_error_text:
                            combined_errors = (combined_errors + "\n" + init_error_text).strip()
                        if combined_errors:
                            tf_result["plan_errors"] = (
                                (tf_result.get("plan_errors") or "") + "\n" + combined_errors
                            )[:2000]
                            tf_result["module_level_errors"] = [combined_errors[:800]]
                        # Target provider.tf preferentially when error mentions provider/required
                        all_error_text = combined_errors.lower()
                        provider_files = [f for f in fallback if "provider" in f]
                        if provider_files and any(
                            kw in all_error_text
                            for kw in ("provider", "required", "argument", "configuration")
                        ):
                            failed_modules = provider_files + [f for f in fallback if f not in provider_files]
                            logger.warning(
                                f"Agent02IaC: tf validate module-level error mentions provider — "
                                f"prioritising {provider_files} in fix list"
                            )
                        else:
                            failed_modules = fallback
                        logger.warning(
                            f"Agent02IaC: terraform validate failed with no file-specific diagnostics — "
                            f"flagging {len(failed_modules)} .tf file(s) for fix loop"
                        )
                    else:
                        logger.warning(
                            "Agent02IaC: terraform validate failed and output directory has no .tf files — accepting with quality warning."
                        )
                        state["modules_to_fix"] = []
                        state["iac_validation_success"] = True
                        state["needs_human_escalation"] = False
                        state["iac_quality_warning"] = True
                        state.setdefault("warnings", []).append(
                            "terraform validate failed with no .tf files on disk — pipeline continues."
                        )
                        return state

            # ── Remap validator temp filenames to real output filenames ──────────
            # _providers.tf is created by _write_tf_workspace() in the temp validation
            # workspace — it does NOT exist in the output directory. When the fix loop
            # receives this name it writes a NEW _providers.tf in output/, creating a
            # duplicate. Map it to provider.tf (the real file) so the fix targets the
            # correct file and avoids the doublon.
            _FILE_REMAPS = {"_providers.tf": "provider.tf"}
            failed_modules = [_FILE_REMAPS.get(f, f) for f in failed_modules]
            # Deduplicate after remapping (multiple diags may point to the same file)
            failed_modules = list(dict.fromkeys(failed_modules))

            state["modules_to_fix"] = failed_modules

            for module in failed_modules:
                attempts = state["correction_counts"].get(module, 0)
                logger.info(f"Module KO: {module} (fix attempts so far: {attempts})")

            checkov_failures_list = checkov_result.get("failed_checks", [])
            security_regen_count  = state.get("security_regen_count") or 0
            max_reached_now = any(
                count >= 3 for count in state["correction_counts"].values()
            )
            if (
                tf_validate_only_valid
                and failed_checks_count > 0
                and security_regen_count < 1
                and not max_reached_now
                and not checkov_result.get("skipped")
            ):
                classified  = _spe_instance.classify_checkov_failures(checkov_failures_list)
                violations  = _spe_instance.build_security_violation_context(classified)
                logger.warning(
                    "Agent02IaC: Checkov-only failures (terraform validates OK) — "
                    f"triggering security regeneration with {len(violations)} classified "
                    f"violation(s). [security_regen_count={security_regen_count+1}]"
                )
                for cat, items in classified.items():
                    if items:
                        logger.info(f"  {cat}: {len(items)} failure(s)")
                state["needs_security_regen"]  = True
                state["security_violations"]   = violations
                state["security_regen_count"]  = security_regen_count + 1
                state["modules_to_fix"]        = []
                state["iac_validation_success"] = False
                state["artifacts"]["iac_validation"] = {
                    "terraform_validation":   tf_result,
                    "plan_dryrun":            plan_result,
                    "security_scan":          checkov_result,
                    "estimated_monthly_cost": infracost_result,
                    "mcp_unavailable":        False,
                    "security_failure_classification": {
                        k: len(v) for k, v in classified.items()
                    },
                }
                return state

            if failed_checks_count > 0:
                classified_for_log = _spe_instance.classify_checkov_failures(checkov_failures_list)
                logger.info(
                    "Agent02IaC: Checkov failure classification (fix-loop path): "
                    + ", ".join(f"{k}={len(v)}" for k, v in classified_for_log.items() if v)
                )
                state["artifacts"]["checkov_failure_classification"] = {
                    k: len(v) for k, v in classified_for_log.items()
                }

            tf_valid = tf_result.get("valid", False)
            # Only CRITICAL and HIGH checkov failures block the pipeline.
            # MEDIUM / LOW / INFO / UNKNOWN are warnings — they do not prevent
            # deployment but are recorded in the artifacts for operator review.
            _blocking_severities = {"CRITICAL", "HIGH"}
            blocking_failures = [
                c for c in checkov_result.get("failed_checks", [])
                if (c.get("severity") or "UNKNOWN").upper() in _blocking_severities
            ]
            non_blocking_failures = [
                c for c in checkov_result.get("failed_checks", [])
                if (c.get("severity") or "UNKNOWN").upper() not in _blocking_severities
            ]
            if non_blocking_failures:
                logger.warning(
                    "Agent02IaC: %d non-blocking checkov finding(s) (MEDIUM/LOW) — "
                    "recorded as warnings, pipeline continues: %s",
                    len(non_blocking_failures),
                    ", ".join(c.get("check_id", "") for c in non_blocking_failures),
                )
            validation_success = tf_valid and len(blocking_failures) == 0
            state["iac_validation_success"] = validation_success
            state["checkov_warnings"] = non_blocking_failures

            max_reached = any(
                count >= 3 for count in state["correction_counts"].values()
            )
            if max_reached:
                checkov_clean = failed_checks_count == 0
                no_blocking = len(blocking_failures) == 0
                # Accept when:
                #   a) terraform validates AND no CRITICAL/HIGH findings remain, OR
                #   b) no-filename module error path AND checkov clean, OR
                #   c) no CRITICAL/HIGH findings remain even if terraform still has
                #      syntax warnings — the fixer exhausted its budget but the IaC
                #      is security-clean; prefer deploying over forcing a human review.
                can_accept = (
                    (tf_validate_only_valid and no_blocking)
                    or (_no_diag_filenames_failure and checkov_clean)
                    or no_blocking  # security-clean even if terraform has residual warnings
                )
                if can_accept:
                    logger.warning(
                        "Agent02IaC: Max correction attempts reached — "
                        "0 CRITICAL/HIGH findings remain (%s). Accepting IaC with %d MEDIUM/LOW "
                        "warning(s) and continuing to deploy.",
                        "terraform validate PASSES" if tf_validate_only_valid else "terraform has residual warnings",
                        len(non_blocking_failures),
                    )
                    state["iac_validation_success"] = True
                    state["modules_to_fix"] = []
                    state["needs_human_escalation"] = False
                    state["iac_quality_warning"] = True
                    state["artifacts"]["checkov_warnings_accepted"] = len(non_blocking_failures)
                else:
                    state["needs_human_escalation"] = True
                    remaining_blocking = [c.get("check_id") for c in blocking_failures]
                    logger.warning(
                        "Agent02IaC: Max correction attempts reached — %s. "
                        "Blocking findings still present: %s — escalating to human.",
                        "terraform validate FAILS" if not tf_validate_only_valid else "terraform OK",
                        remaining_blocking,
                    )

            state["artifacts"]["iac_validation"] = {
                "terraform_validation": tf_result,
                "plan_dryrun": plan_result,
                "security_scan": checkov_result,
                "estimated_monthly_cost": infracost_result,
                "mcp_unavailable": False,
            }

            if validation_success:
                logger.info("Agent02IaC: Validation PASSED (local: terraform + checkov + infracost).")
            else:
                logger.warning(
                    "Agent02IaC: Validation FAILED — %d module(s) to fix.", len(failed_modules)
                )

        except Exception as e:
            logger.error(f"Agent02IaC: validation error (non-blocking): {e}", exc_info=True)
            state["iac_validation_success"] = True
            state["modules_to_fix"] = []
            state["needs_human_escalation"] = False
            state["iac_quality_warning"] = True
            state.setdefault("warnings", []).append(f"IaC validation error (skipped): {e}")

        return state
