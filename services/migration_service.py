"""
migration_service.py - Business logic for Migration entity.

Orchestrates the LangGraph agent pipeline and persists results to the database.
"""
import logging
import os
import uuid
from typing import List, Optional

from api.schemas.migration_schema import MigrationCreate, MigrationUpdatePartial
from core.enums import MigrationStatus
from data.models.migration_model import Migration
from data.repositories.migration_repository import MigrationRepository
from data.custom_data_exceptions import (
    MigrationNotFoundDB,
    MigrationAlreadyExistsDB,
    GeneralDatabaseError,
)
from services.custom_service_exceptions import (
    MigrationDoesNotExist,
    MigrationAlreadyExists,
    MigrationInvalidState,
    MissingCredentialsError,
    AgentExecutionError,
    ServiceDBError,
)
from core.token_encryption import decrypt_token  # backward compat: old migrations may have Fernet token in DB
from services.credentials.vault_store import get_credential_store
from services.credentials.broker import get_secret_broker

logger = logging.getLogger("MigrationService")

# Tracks which unscoped-access call sites have already warned, so the legacy
# "no caller_id" path logs once at WARNING instead of on every single request.
_UNSCOPED_WARNED: set[str] = set()


def _warn_unscoped_once(call_site: str) -> None:
    if call_site not in _UNSCOPED_WARNED:
        _UNSCOPED_WARNED.add(call_site)
        logger.warning(
            "%s called without caller_id — ownership scoping DISABLED for "
            "API-key-only clients (frontend sends no JWT Bearer). This is logged "
            "once; subsequent occurrences are at debug level.",
            call_site,
        )


def _normalize_cloud(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = getattr(value, "value")
    return str(value).lower().strip()


class MigrationService:
    """Service layer for Migration - orchestrates agents + persistence."""

    def __init__(self, repo: MigrationRepository):
        self.repo = repo

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def get_by_id(self, migration_id: str) -> Migration:
        """Unscoped get — internal pipeline use only (background tasks, agent callbacks).

        HTTP handlers MUST use get_by_id_for_user() instead so ownership is enforced.
        This method intentionally bypasses ownership: callers are trusted internal code
        (LangGraph background tasks, SSE, runner callbacks) that have no HTTP context.
        """
        try:
            return await self.repo.get_by_id(migration_id)
        except MigrationNotFoundDB:
            raise MigrationDoesNotExist()
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    async def get_by_id_for_user(
        self,
        migration_id: str,
        caller_id: Optional[str],
        is_admin: bool = False,
    ) -> Migration:
        """Ownership-scoped get. Use this from all HTTP handlers."""
        try:
            if caller_id is None:
                # Legacy call without JWT — unscoped (backward-compat).
                # The frontend is still API-key-only (no Bearer), so this fires on
                # EVERY request. Warn once at startup, then stay at debug to avoid
                # drowning real signal in the logs.
                _warn_unscoped_once("get_by_id_for_user")
                logger.debug(
                    "get_by_id_for_user without caller_id for migration=%s — unscoped",
                    migration_id,
                )
                return await self.repo.get_by_id(migration_id)
            return await self.repo.get_by_id_for_user(migration_id, caller_id, is_admin)
        except MigrationNotFoundDB:
            raise MigrationDoesNotExist()
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    async def get_all(self, skip: int = 0, limit: int = 20) -> List[Migration]:
        """Unscoped list — kept for admin/internal use."""
        try:
            return await self.repo.get_all(skip=skip, limit=limit)
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    async def get_all_for_user(
        self,
        caller_id: Optional[str],
        is_admin: bool = False,
        skip: int = 0,
        limit: int = 20,
    ) -> List[Migration]:
        """Ownership-scoped list. Use this from all HTTP handlers."""
        try:
            if caller_id is None:
                _warn_unscoped_once("get_all_for_user")
                logger.debug("get_all_for_user without caller_id — unscoped")
                return await self.repo.get_all(skip=skip, limit=limit)
            return await self.repo.get_all_for_user(caller_id, is_admin, skip=skip, limit=limit)
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    async def create(self, data: MigrationCreate, owner_id: Optional[str] = None) -> Migration:
        migration_data = data.model_dump()
        # Pop non-column fields before building the ORM object
        creds_pre_validated = migration_data.pop("credentials_pre_validated", False)
        owned_repos = migration_data.pop("owned_repos", None)
        # Extract token before DB insert — stored in Vault, never in PostgreSQL
        raw_token = migration_data.pop("github_token", None)
        creation_artifacts: dict = {"credentials_pre_validated": bool(creds_pre_validated)}
        if owned_repos:
            creation_artifacts["owned_repos"] = list(owned_repos)
        # Use the same UUID for both id and thread_id so the SSE stream endpoint
        # (which queries pipeline_events by migration_id) matches the thread_id
        # stored by EventPublisher — no ID mapping needed anywhere.
        migration_uuid = str(uuid.uuid4())
        new_migration = Migration(
            id=migration_uuid,
            **migration_data,
            github_token=None,
            thread_id=migration_uuid,
            artifacts=creation_artifacts,
            user_id=owner_id,
        )
        try:
            created = await self.repo.create(new_migration)
        except MigrationAlreadyExistsDB as e:
            raise MigrationAlreadyExists(str(e)) from e
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

        if raw_token:
            try:
                store = get_credential_store()
                store.store(
                    user_id=str(created.id),
                    provider="github_token",
                    creds={"token": raw_token},
                )
                logger.info(f"create: GitHub token stored in Vault for migration {created.id}")
            except Exception as e:
                logger.error(f"create: Vault store failed for migration {created.id}: {e}")
        return created

    async def update_by_id(
        self,
        migration_id: str,
        data: MigrationUpdatePartial,
    ) -> Migration:
        update_dict = data.model_dump(exclude_unset=True)

        # credentials_pre_validated is stored inside the artifacts JSON column,
        # not as a direct DB column. Extract it and merge into artifacts so the
        # repo layer (which uses setattr) does not silently drop it.
        creds_flag = update_dict.pop("credentials_pre_validated", None)
        if creds_flag is not None:
            try:
                current = await self.repo.get_by_id(migration_id)
                merged_artifacts = dict(current.artifacts or {})
                merged_artifacts["credentials_pre_validated"] = bool(creds_flag)
                update_dict["artifacts"] = merged_artifacts
            except Exception as _merge_exc:
                logger.warning(
                    "update_by_id: could not merge credentials_pre_validated into artifacts "
                    "for migration=%s: %s", migration_id, _merge_exc
                )

        try:
            return await self.repo.update_by_id(migration_id, update_dict)
        except MigrationNotFoundDB:
            raise MigrationDoesNotExist()
        except MigrationAlreadyExistsDB as e:
            raise MigrationAlreadyExists(str(e)) from e
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    async def delete_by_id(self, migration_id: str) -> None:
        try:
            await self.repo.delete_by_id(migration_id)
        except MigrationNotFoundDB:
            raise MigrationDoesNotExist()
        except GeneralDatabaseError as e:
            raise ServiceDBError(str(e)) from e

    # ── Workflow actions ──────────────────────────────────────────────────

    async def start_analysis(self, migration_id: str) -> Migration:
        """Launch the LangGraph analysis pipeline (Agent 01 — planning).

        Runs: analyze_repo → check_analysis → cooldown → build_plan → check_plan
        then pauses before ask_human (interrupt_before).
        """
        migration = await self.get_by_id(migration_id)

        if migration.status not in (
            MigrationStatus.CREATED,
            MigrationStatus.ANALYSIS_FAILED,
        ):
            raise MigrationInvalidState(
                f"Cannot start analysis from status '{migration.status}'. "
                "Must be 'Created' or 'Analysis_Failed'."
            )

        # Update status to ANALYZING
        await self.repo.update_by_id(migration_id, {"status": MigrationStatus.ANALYZING})

        try:
            # Vault-first: GitHub token stored in Vault at creation time.
            # Fallback to DB for backward compat (migrations created before Vault integration).
            github_token: str | None = None
            try:
                store = get_credential_store()
                creds = store.get(user_id=str(migration_id), provider="github_token")
                github_token = creds.get("token") if creds else None
            except Exception as e:
                logger.warning(f"start_analysis: Vault lookup failed ({e}) — trying DB fallback")
            if not github_token and migration.github_token:
                github_token = decrypt_token(migration.github_token)

            if not github_token:
                from configuration.settings import settings
                github_token = settings.GITHUB_TOKEN
                logger.info(
                    "start_analysis: token client absent — "
                    "utilisation du token serveur (fallback)"
                )

            if not github_token:
                raise AgentExecutionError(
                    "Aucun token GitHub disponible. "
                    "Fournissez un token à la création de la migration."
                )

            logger.info("start_analysis: GitHub token disponible")

            # Build initial LangGraph state
            source_cloud = _normalize_cloud(migration.source_cloud)
            target_cloud = _normalize_cloud(migration.target_cloud)
            # credentials_pre_validated and owned_repos are stashed in artifacts at creation time
            _artifacts = migration.artifacts or {}
            creds_pre_validated = bool(_artifacts.get("credentials_pre_validated", False))
            owned_repos: list = list(_artifacts.get("owned_repos") or [])
            # Build effective repo_urls list: explicit multi-repo list takes priority;
            # fall back to single repo_url for backward compat.
            repo_urls: list = migration.repo_urls or [migration.repo_url]

            initial_state = {
                "thread_id": migration.thread_id,
                "repo_url": migration.repo_url,
                "repo_urls": repo_urls,
                "source_cloud": source_cloud,
                "target_cloud": target_cloud,
                "ai_stack": migration.ai_stack or {},
                "monthly_budget_usd": migration.monthly_budget_usd,
                "timeline": migration.timeline or "",
                "target_region": migration.target_region or "",
                "data_residency_requirement": migration.data_residency_requirement or "",
                "regulatory_constraints": migration.regulatory_constraints or "",
                "compliance_standards": list(migration.compliance_standards or []),
                "high_availability_required": bool(migration.high_availability_required),
                "network_isolation_required": bool(migration.network_isolation_required),
                "credentials_pre_validated": creds_pre_validated,
                "owned_repos": owned_repos,
                "migration_id": str(migration.id),
                "github_token": github_token,
                "partial_rejection": False,
                "rejected_services": [],
                "rejection_reasons": {},
                "excluded_services": [],
                "rejection_count": 0,
                "source_files": {},
                "errors": [],
                "warnings": [],
            }

            from pipeline.orchestrator import get_orchestrator
            result = await get_orchestrator().start_analysis(initial_state)

            # Persist agent outputs to database (JSON blob)
            # If the pipeline interrupted at ask_user_services, keep ANALYZING so
            # submit_services can resume. Embed the flag in dependency_graph so the
            # frontend can show ServiceSelectionChecklist without relying on SSE.
            needs_svc_sel = bool(result.get("needs_service_selection"))
            dep_graph = result.get("dependency_graph") or {}
            if needs_svc_sel:
                dep_graph = {**dep_graph, "needs_service_selection": True}
            update_data = {
                "status": MigrationStatus.ANALYZING if needs_svc_sel else MigrationStatus.PLAN_READY,
                "dependency_graph": dep_graph,
                "migration_plan": result.get("migration_plan"),
                "preview_report": (result.get("artifacts") or {}).get("preview_report", ""),
                "errors": result.get("errors", []),
            }
            migration = await self.repo.update_by_id(migration_id, update_data)

            # Persist detailed dependency graph to normalized PostgreSQL tables
            try:
                from data.repositories.dependency_saver import DependencySaver
                from configuration.database import async_session as _async_session

                async with _async_session() as db_session:
                    saver = DependencySaver(db_session)
                    await saver.save_dependency_graph(
                        migration_id=migration_id,
                        dependency_graph=result.get("dependency_graph") or {},
                        source_services_inventory=result.get("source_services_inventory") or [],
                        migration_priority=result.get("migration_priority") or [],
                    )
                logger.info(f"Detailed dependency graph saved to PostgreSQL for {migration_id}")
            except Exception as save_exc:
                # Non-blocking: JSON blob is already saved above
                logger.warning(f"Could not save detailed graph tables: {save_exc}")

            return migration

        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            logger.error(f"Analysis failed for migration {migration_id}: {error_detail}", exc_info=True)
            await self.repo.update_by_id(migration_id, {
                "status": MigrationStatus.ANALYSIS_FAILED,
                "errors": [error_detail],
            })
            raise AgentExecutionError(f"Analysis failed: {error_detail}") from exc

    async def accept_plan(self, migration_id: str) -> Migration:
        """User accepts the migration plan — resumes pipeline into Agent 02 (IaC generation)."""
        migration = await self.get_by_id(migration_id)

        acceptable_statuses = {MigrationStatus.PLAN_READY, MigrationStatus.FAILED}
        if migration.status not in acceptable_statuses:
            raise MigrationInvalidState(
                f"Cannot accept plan from status '{migration.status}'. Must be 'Plan_Ready'."
            )
        if migration.status == MigrationStatus.FAILED:
            if not (migration.artifacts or {}).get("migration_plan"):
                raise MigrationInvalidState(
                    "Cannot retry a failed migration that has no plan. Start a new migration."
                )
            await self.repo.update_by_id(migration_id, {"status": MigrationStatus.PLAN_READY})
            migration = await self.get_by_id(migration_id)

        # Fail-fast: if the user flagged credentials_pre_validated=True at creation
        # time, the target-cloud vault entry must exist before Agent 02 starts.
        # Cloud creds are stored by POST /credentials/store via the broker at
        # migrations/{id}/{provider} — NOT in the vault_store users/ namespace.
        creds_pre_validated = bool((migration.artifacts or {}).get("credentials_pre_validated", False))
        if creds_pre_validated:
            _target_cloud = _normalize_cloud(migration.target_cloud)
            if _target_cloud:
                missing: list[str] = []
                try:
                    _broker = get_secret_broker()
                    _path = _broker.migration_path(str(migration_id), _target_cloud)
                    if not _broker.get(_path):
                        missing.append(_target_cloud)
                except Exception as vault_check_exc:
                    # Vault unreachable (connection error, sealed, not initialized) —
                    # fall back to dry-run mode instead of blocking the user.
                    # Only raise MissingCredentialsError when Vault IS reachable
                    # but the secret is genuinely absent.
                    logger.warning(
                        "accept_plan: vault pre-check failed (%s) — "
                        "falling back to dry-run (credentials_pre_validated=False)",
                        vault_check_exc,
                    )
                    creds_pre_validated = False
                if missing:
                    raise MissingCredentialsError(missing=missing)

        await self.repo.update_by_id(migration_id, {"status": MigrationStatus.GENERATING_IAC})

        try:
            from pipeline.orchestrator import get_orchestrator
            result = await get_orchestrator().accept_plan(migration.thread_id)

            # Persist Agent 02+ outputs
            artifacts = result.get("artifacts") or {}
            generation_error = artifacts.get("generation_error")
            iac_validation_success = bool(result.get("iac_validation_success", False))
            needs_human_escalation = bool(result.get("needs_human_escalation", False))

            migration_plan_out = result.get("migration_plan") or migration.migration_plan or {}
            expected_tf_count = 0
            for resource in (migration_plan_out or {}).get("resources", []):
                strategy = (resource.get("strategy") or "").upper()
                if strategy not in {"RETAIN", "RETIRE"}:
                    expected_tf_count += 1

            terraform_code = artifacts.get("terraform_code")
            has_terraform = isinstance(terraform_code, str) and bool(terraform_code.strip())

            # Agent 02 generates separate .tf files rather than a single terraform_code string.
            # Also accept generated_code_files or disk-based .tf files as valid proof of IaC output.
            if not has_terraform and expected_tf_count > 0:
                gen_files_check = (
                    result.get("generated_code_files")
                    or artifacts.get("generated_files")
                    or result.get("generated_files")
                    or []
                )
                has_terraform = bool(gen_files_check) or bool(artifacts.get("iac_output"))
                if not has_terraform and migration_id:
                    try:
                        from core.paths import get_output_dir
                        tf_on_disk = list(get_output_dir(migration_id).glob("*.tf"))
                        has_terraform = len(tf_on_disk) > 0
                    except Exception:
                        pass

            missing_expected_tf = expected_tf_count > 0 and not has_terraform

            status_ok = (
                iac_validation_success
                and not needs_human_escalation
                and not generation_error
                and not missing_expected_tf
            )

            final_status = MigrationStatus.COMPLETED if status_ok else MigrationStatus.FAILED
            _raw_dep_status = result.get("deployment_status", "completed")
            # completed_with_warnings is a valid success state (health checks had non-blocking warnings)
            deployment_status = _raw_dep_status if status_ok else "failed"

            persisted_errors = list(result.get("errors", []))
            if generation_error:
                persisted_errors.append(f"Agent02 generation error: {generation_error}")
            if missing_expected_tf:
                persisted_errors.append(
                    f"No Terraform generated for {expected_tf_count} resource(s) requiring IaC"
                )
            if not iac_validation_success:
                persisted_errors.append("IaC validation did not pass")
            if needs_human_escalation:
                persisted_errors.append("Pipeline requires human escalation")

            if not status_ok:
                logger.error(
                    "accept_plan: pipeline finished with non-success status "
                    f"(generation_error={bool(generation_error)}, "
                    f"iac_validation_success={iac_validation_success}, "
                    f"needs_human_escalation={needs_human_escalation}, "
                    f"missing_expected_tf={missing_expected_tf})"
                )

            gen_files = (
                result.get("generated_code_files")
                or artifacts.get("generated_files")
                or result.get("generated_files")
            )
            # Extract GitHub publish results from pipeline state (publish_github_node)
            github_pr_url   = result.get("github_pr_url")  or artifacts.get("github_pr_url")  or ""
            github_repo_url = result.get("github_repo_url") or artifacts.get("github_repo_url") or ""

            # Merge correction_counts into artifacts so it survives DB serialization
            # (correction_counts lives in LangGraph in-memory state, not a DB column)
            correction_counts = result.get("correction_counts") or {}
            if correction_counts:
                artifacts = {**artifacts, "correction_counts": correction_counts}

            update_data = {
                "status": final_status,
                "iac_output": artifacts.get("iac_output") or result.get("iac_output"),
                "generated_files": gen_files,
                "deployment_status": deployment_status,
                "artifacts": artifacts,
                "errors": persisted_errors,
                "github_pr_url": github_pr_url or None,
                "github_repo_url": github_repo_url or None,
            }

            # Livraison des artefacts vers GitHub — FALLBACK uniquement.
            # Le mécanisme nominal est publish_github_node (pipeline LangGraph), qui
            # ouvre une Pull Request sur le dépôt cible (cf. rapport, Phase 4 : "ouverture
            # automatique d'une pull request pour revue humaine"). deliver_artifacts_to_github
            # fait un push direct dans un AUTRE dépôt et ne doit donc PAS s'exécuter quand
            # publish_github_node a déjà livré — sinon on crée deux dépôts pour une migration.
            # On ne le déclenche que si le pipeline n'a produit aucune URL (deploy sauté,
            # publish_github non atteint).
            already_delivered = bool(github_pr_url or github_repo_url)
            if status_ok and not already_delivered:
                _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
                _out_dir = (
                    _out_env if (_out_env and os.path.isabs(_out_env))
                    else os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        _out_env or os.path.join("output", "migrated_app"),
                    )
                )
                from services.migration_artifact_service import deliver_artifacts_to_github
                delivery = deliver_artifacts_to_github(
                    migration_id=migration_id,
                    db_encrypted_token=migration.github_token,
                    artifacts=artifacts,
                    gen_files=gen_files,
                    migration_plan=migration_plan_out,
                    # _normalize_cloud unwraps the CloudProvider enum to its .value
                    # ("azure") — str(enum) would yield "CloudProvider.AZURE" and corrupt
                    # the generated repo name.
                    target_cloud=_normalize_cloud(migration.target_cloud),
                    source_repo=str(migration.repo_url or ""),
                    output_dir=_out_dir,
                )
                if delivery:
                    merged_artifacts = {**artifacts, "github_delivery": delivery}
                    update_data["artifacts"] = merged_artifacts
                    # Also persist top-level fields from artifact_service delivery if
                    # publish_github_node didn't run (e.g., deploy was skipped)
                    if not update_data.get("github_pr_url") and delivery.get("pr_url"):
                        update_data["github_pr_url"] = delivery["pr_url"]
                    if not update_data.get("github_repo_url") and delivery.get("repo_url"):
                        update_data["github_repo_url"] = delivery["repo_url"]

            return await self.repo.update_by_id(migration_id, update_data)

        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            logger.error(f"IaC generation failed for migration {migration_id}: {error_detail}", exc_info=True)
            await self.repo.update_by_id(migration_id, {
                "status": MigrationStatus.FAILED,
                "errors": [error_detail],
            })
            raise AgentExecutionError(f"IaC generation failed: {error_detail}") from exc

    async def record_deploy_outcome(self, migration_id: str, result: dict) -> None:
        """Persist the final deploy/apply outcome after the graph resumes post-runner.

        accept_plan() writes status=COMPLETED as soon as IaC validation passes —
        BEFORE the actual `terraform apply` runs (deploy is enqueued asynchronously
        via the runner queue + callback). Without this method, the migrations row
        stays frozen at that pre-deploy snapshot even when the real deployment later
        fails (e.g. ENVIRONMENTAL apply-stage failure → escalate), causing the
        frontend (which reads `status`/`deployment_status` from the DB) to look
        "stuck" in the IaC-generation phase while the backend has already finished
        (successfully or not) in the background.

        Called from POST /runner-callback after resume_after_runner() returns the
        graph's final state.
        """
        deployment_status = result.get("deployment_status") or "unknown"
        runner_failure_class = result.get("runner_failure_class")
        needs_human_escalation = bool(result.get("needs_human_escalation", False))

        deploy_failed = deployment_status not in ("deployed", "completed", "completed_with_warnings") or bool(runner_failure_class)
        final_status = (
            MigrationStatus.FAILED if (deploy_failed or needs_human_escalation)
            else MigrationStatus.COMPLETED
        )

        migration = await self.get_by_id(migration_id)
        persisted_errors = list(migration.errors or [])
        if runner_failure_class:
            history = result.get("runner_failure_history") or []
            last = history[-1] if history else {}
            stage = last.get("stage") or "deploy"
            persisted_errors.append(
                f"Deployment failed at stage '{stage}' (class={runner_failure_class})"
                + (" — partial cloud state may exist, human review required" if needs_human_escalation else "")
            )
        elif deploy_failed:
            persisted_errors.append(f"Deployment finished with status '{deployment_status}'")

        update_data: dict = {
            "status": final_status,
            "deployment_status": deployment_status,
            "errors": persisted_errors,
        }
        artifacts = result.get("artifacts")
        if artifacts:
            merged_artifacts = {**(migration.artifacts or {}), **artifacts}
            update_data["artifacts"] = merged_artifacts
            github_pr_url  = result.get("github_pr_url")  or artifacts.get("github_pr_url")
            github_repo_url = result.get("github_repo_url") or artifacts.get("github_repo_url")
            if github_pr_url:
                update_data["github_pr_url"] = github_pr_url
            if github_repo_url:
                update_data["github_repo_url"] = github_repo_url

        await self.repo.update_by_id(migration_id, update_data)
        logger.info(
            "record_deploy_outcome: migration=%s final_status=%s deployment_status=%s "
            "runner_failure_class=%s needs_human_escalation=%s",
            migration_id, final_status, deployment_status, runner_failure_class, needs_human_escalation,
        )

    async def partial_reject_plan(
        self,
        migration_id: str,
        rejected_services: list[dict],
        rejection_reasons: dict[str, str],
    ) -> Migration:
        """User rejects specific services — re-run Agent 01 only for those services."""
        migration = await self.get_by_id(migration_id)

        if migration.status != MigrationStatus.PLAN_READY:
            raise MigrationInvalidState(
                f"Cannot partially reject plan from status '{migration.status}'. "
                "Must be 'Plan_Ready'."
            )

        await self.repo.update_by_id(migration_id, {"status": MigrationStatus.CORRECTING})

        try:
            from pipeline.orchestrator import get_orchestrator
            result = await get_orchestrator().partial_reject(
                migration.thread_id, rejected_services, rejection_reasons
            )

            update_data = {
                "status": MigrationStatus.PLAN_READY,
                "migration_plan": result.get("migration_plan"),
                "preview_report": (result.get("artifacts") or {}).get("preview_report", ""),
                "errors": result.get("errors", []),
            }
            return await self.repo.update_by_id(migration_id, update_data)

        except Exception as exc:
            logger.error(
                f"Partial rejection failed for migration {migration_id}: {exc}",
                exc_info=True,
            )
            await self.repo.update_by_id(migration_id, {
                "status": MigrationStatus.PLAN_READY,   # revert — plan still available
                "errors": [str(exc)],
            })
            raise AgentExecutionError(f"Partial rejection failed: {exc}") from exc

    async def reject_plan(self, migration_id: str) -> Migration:
        """User rejects the plan - triggers ZIP export."""
        migration = await self.get_by_id(migration_id)

        if migration.status != MigrationStatus.PLAN_READY:
            raise MigrationInvalidState(
                f"Cannot reject plan from status '{migration.status}'. Must be 'Plan_Ready'."
            )

        await self.repo.update_by_id(migration_id, {"status": MigrationStatus.REJECTED})

        try:
            from pipeline.orchestrator import get_orchestrator
            result = await get_orchestrator().reject_plan(migration.thread_id)

            update_data = {
                "status": MigrationStatus.EXPORTED,
                "deployment_status": result.get("deployment_status", "exported"),
                "errors": result.get("errors", []),
            }
            return await self.repo.update_by_id(migration_id, update_data)

        except Exception as exc:
            logger.error(f"Export failed for migration {migration_id}: {exc}", exc_info=True)
            await self.repo.update_by_id(migration_id, {
                "status": MigrationStatus.FAILED,
                "errors": [str(exc)],
            })
            raise AgentExecutionError(f"Export failed: {exc}") from exc

    async def submit_services(
        self,
        migration_id: str,
        services: list[dict],
    ) -> Migration:
        """User submits manually-selected cloud services (no-IaC path).

        Injects the selection into the graph checkpoint at ask_user_services,
        then resumes the pipeline through cooldown → build_plan → ask_human.
        The graph pauses again at ask_human (interrupt_before) waiting for plan approval.
        """
        migration = await self.get_by_id(migration_id)

        if migration.status not in (
            MigrationStatus.ANALYZING,
            MigrationStatus.CREATED,
        ):
            raise MigrationInvalidState(
                f"Cannot submit services from status '{migration.status}'. "
                "The pipeline must be paused at service selection."
            )

        await self.repo.update_by_id(migration_id, {"status": MigrationStatus.ANALYZING})

        try:
            from pipeline.orchestrator import get_orchestrator
            result = await get_orchestrator().submit_services(migration.thread_id, services)

            dep_graph = result.get("dependency_graph") or {}
            dep_graph = {k: v for k, v in dep_graph.items() if k != "needs_service_selection"}
            update_data = {
                "status": MigrationStatus.PLAN_READY,
                "dependency_graph": dep_graph,
                "migration_plan":   result.get("migration_plan"),
                "preview_report":   (result.get("artifacts") or {}).get("preview_report", ""),
                "errors":           result.get("errors", []),
            }
            return await self.repo.update_by_id(migration_id, update_data)

        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            logger.error(f"submit_services failed for {migration_id}: {error_detail}", exc_info=True)
            await self.repo.update_by_id(migration_id, {
                "status": MigrationStatus.ANALYSIS_FAILED,
                "errors": [error_detail],
            })
            raise AgentExecutionError(f"Service submission failed: {error_detail}") from exc
