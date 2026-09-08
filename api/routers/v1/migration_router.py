"""
migration_router.py - REST endpoints for Migration entity.

CRUD: GET / POST / PATCH / DELETE
Workflow: POST /analyze, POST /accept, POST /reject
"""
from typing import List
from fastapi import APIRouter, Depends, Path, Query, Request, status

from api.custom_api_exceptions import (
    NotFoundError,
    ConflictError,
    UnprocessableError,
    InternalServerError,
)
from api.dependencies import get_migration_service, get_caller_identity, CallerIdentity
from api.schemas.migration_schema import (
    MigrationCreate,
    MigrationResponse,
    MigrationSummaryResponse,
    MigrationUpdatePartial,
    PartialRejectRequest,
    AnalysisResponse,
    DecisionResponse,
    ServiceSelectionRequest,
    ServiceSelectionResponse,
)
from services.custom_service_exceptions import (
    MigrationDoesNotExist,
    MigrationAlreadyExists,
    MigrationInvalidState,
    MissingCredentialsError,
    AgentExecutionError,
    ServiceDBError,
)
from services.migration_service import MigrationService
from services.audit_service import audit
from api.rate_limiter import limiter, SLOWAPI_AVAILABLE
from configuration.settings import settings
from api.auth.api_key import require_api_key
from api.auth.jwt_handler import TokenData
from configuration.database import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/migrations",
    tags=["MIGRATIONS"],
    dependencies=[Depends(require_api_key)],
)


# ── CRUD endpoints ───────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=List[MigrationSummaryResponse],
    status_code=status.HTTP_200_OK,
)
async def get_all_migrations(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """List migrations scoped to the caller's identity.

    - Admin JWT   → all migrations
    - Analyst JWT → own migrations only
    - No JWT      → all migrations (legacy API-key-only mode, deprecated)
    """
    return await service.get_all_for_user(
        caller_id=caller.user_id,
        is_admin=caller.is_admin,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{migration_id}",
    response_model=MigrationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_migration(
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Get migration details, scoped to the caller's identity."""
    try:
        return await service.get_by_id_for_user(
            migration_id, caller.user_id, caller.is_admin
        )
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.post(
    "/",
    response_model=MigrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_migration(
    request: Request,
    data: MigrationCreate,
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new migration job, owned by the authenticated user."""
    try:
        result = await service.create(data, owner_id=caller.user_id)
        await audit(db, action="migration.created", request=request,
                    resource_type="migration", resource_id=result.id,
                    resource_name=data.repo_url,
                    details={
                        "source": data.source_cloud,
                        "target": data.target_cloud,
                        "owner_id": caller.user_id,
                    })
        return result
    except MigrationAlreadyExists as e:
        raise ConflictError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.patch(
    "/{migration_id}",
    response_model=MigrationResponse,
    status_code=status.HTTP_200_OK,
)
async def update_migration(
    migration_id: str = Path(...),
    data: MigrationUpdatePartial = ...,
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Partially update a migration (owner or admin only)."""
    try:
        # Ownership check before update
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        return await service.update_by_id(migration_id, data)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except MigrationAlreadyExists as e:
        raise ConflictError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.delete(
    "/{migration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_migration(
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Delete a migration (owner or admin only)."""
    try:
        # Ownership check before delete
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        await service.delete_by_id(migration_id)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


# ── Workflow endpoints ────────────────────────────────────────────────────────

@router.post(
    "/{migration_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
)
@(limiter.limit(settings.RATE_LIMIT_ANALYZE) if SLOWAPI_AVAILABLE else lambda f: f)
async def start_analysis(
    request: Request,
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Launch the LangGraph analysis pipeline asynchronously.

    Returns 202 immediately. The pipeline runs in the background and updates
    migration.status (ANALYZING → PLAN_READY | ANALYSIS_FAILED) in DB.
    The frontend tracks progress via SSE (/api/v1/sse/{thread_id}).

    Runs: analyze_repo → check_analysis → cooldown → build_plan → check_plan.
    """
    import asyncio

    try:
        migration = await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))

    from core.enums import MigrationStatus
    if migration.status not in (MigrationStatus.CREATED, MigrationStatus.ANALYSIS_FAILED):
        raise UnprocessableError(
            detail=f"Cannot start analysis from status '{migration.status}'. "
                   "Must be 'Created' or 'Analysis_Failed'."
        )

    async def _run_analysis():
        # Re-create the service with a fresh DB session for the background task
        from configuration.database import async_session
        from data.repositories.migration_repository import MigrationRepository as _Repo
        from services.migration_service import MigrationService as _Svc
        async with async_session() as db:
            bg_service = _Svc(_Repo(db))
            try:
                await bg_service.start_analysis(migration_id)
            except Exception as exc:
                import logging
                logging.getLogger("migration.analyze_bg").error(
                    "Background analysis failed for %s: %s", migration_id, exc
                )

    asyncio.ensure_future(_run_analysis())

    return {
        "migration_id": migration_id,
        "status": "analyzing",
        "message": "Analysis started. Track progress via SSE.",
        "thread_id": migration.thread_id,
    }


@router.post(
    "/{migration_id}/accept",
    status_code=status.HTTP_202_ACCEPTED,
)
@(limiter.limit(settings.RATE_LIMIT_ACCEPT) if SLOWAPI_AVAILABLE else lambda f: f)
async def accept_plan(
    request: Request,
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Accept the migration plan and trigger IaC generation + deployment asynchronously.

    Returns 202 immediately. The pipeline runs in the background and updates
    migration.status in DB. Track progress via SSE (/api/v1/sse/{thread_id}).
    """
    import asyncio

    try:
        migration = await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))

    # Fail-fast checks that must run synchronously (before 202 is returned)
    from core.enums import MigrationStatus
    acceptable = {MigrationStatus.PLAN_READY, MigrationStatus.FAILED}
    if migration.status not in acceptable:
        raise UnprocessableError(
            detail=f"Cannot accept plan from status '{migration.status}'. Must be 'Plan_Ready'."
        )

    # Credential pre-validation check (fast Vault lookup — keeps user-visible error sync)
    from services.migration_service import _normalize_cloud
    from services.credentials.broker import get_secret_broker
    creds_pre_validated = bool((migration.artifacts or {}).get("credentials_pre_validated", False))
    if creds_pre_validated:
        _target_cloud = _normalize_cloud(migration.target_cloud)
        if _target_cloud:
            try:
                broker = get_secret_broker()
                path = broker.migration_path(str(migration.id), _target_cloud)
                if not broker.get(path):
                    from services.custom_service_exceptions import MissingCredentialsError
                    raise MissingCredentialsError(missing=[_target_cloud])
            except MissingCredentialsError:
                raise UnprocessableError(detail={"error": "missing_credentials", "missing": [_target_cloud]})
            except Exception:
                pass  # Vault unreachable → dry-run mode

    async def _run_accept():
        from configuration.database import async_session
        from data.repositories.migration_repository import MigrationRepository as _Repo
        from services.migration_service import MigrationService as _Svc
        async with async_session() as db:
            bg_service = _Svc(_Repo(db))
            try:
                await bg_service.accept_plan(migration_id)
            except Exception as exc:
                import logging
                logging.getLogger("migration.accept_bg").error(
                    "Background accept failed for %s: %s", migration_id, exc
                )

    asyncio.ensure_future(_run_accept())

    return {
        "migration_id": migration_id,
        "status": "generating_iac",
        "message": "Plan accepted. IaC generation started. Track progress via SSE.",
        "thread_id": migration.thread_id,
    }


@router.post(
    "/{migration_id}/reject",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
)
@(limiter.limit(settings.RATE_LIMIT_REJECT) if SLOWAPI_AVAILABLE else lambda f: f)
async def reject_plan(
    request: Request,
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Reject the migration plan entirely — exports a ZIP archive and ends the pipeline."""
    try:
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        return await service.reject_plan(migration_id)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except MigrationInvalidState as e:
        raise UnprocessableError(detail=str(e))
    except AgentExecutionError as e:
        raise InternalServerError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.post(
    "/{migration_id}/partial-reject",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
)
@(limiter.limit(settings.RATE_LIMIT_ANALYZE) if SLOWAPI_AVAILABLE else lambda f: f)
async def partial_reject_plan(
    request: Request,
    migration_id: str = Path(...),
    body: PartialRejectRequest = ...,
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Partially reject specific services — Agent 01 re-runs only for rejected services.

    The corrected plan is returned with status Plan_Ready for a new user decision.
    """
    try:
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        return await service.partial_reject_plan(
            migration_id,
            rejected_services=body.rejected_services,
            rejection_reasons=body.rejection_reasons,
        )
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except MigrationInvalidState as e:
        raise UnprocessableError(detail=str(e))
    except AgentExecutionError as e:
        raise InternalServerError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.post(
    "/{migration_id}/submit-services",
    response_model=ServiceSelectionResponse,
    status_code=status.HTTP_200_OK,
)
@(limiter.limit(settings.RATE_LIMIT_ANALYZE) if SLOWAPI_AVAILABLE else lambda f: f)
async def submit_services(
    request: Request,
    migration_id: str = Path(...),
    body: ServiceSelectionRequest = ...,
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Submit manually-selected cloud services when the repo has no IaC files.

    Resumes the pipeline from the ask_user_services checkpoint.
    Runs: ask_user_services → cooldown → build_plan → check_plan.
    Pauses again at ask_human for plan approval.
    """
    try:
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        return await service.submit_services(
            migration_id,
            services=[s.model_dump() for s in body.services],
        )
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except MigrationInvalidState as e:
        raise UnprocessableError(detail=str(e))
    except AgentExecutionError as e:
        raise InternalServerError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))


@router.post(
    "/{migration_id}/retry-validation",
    response_model=MigrationResponse,
    status_code=status.HTTP_200_OK,
)
async def retry_validation(
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Reset escalation flags and reload fixed .tf files from disk into artifacts.

    Clears needs_human_escalation / correction_counts / iac_quality_warning and
    rebuilds terraform_code from the .tf files currently on disk, so the UI
    shows the corrected files without re-running the full pipeline.
    """
    try:
        await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
        return await service.retry_validation(migration_id)
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except ServiceDBError as e:
        raise InternalServerError(detail=str(e))



@router.get(
    "/{migration_id}/sync-status",
    status_code=status.HTTP_200_OK,
)
async def get_sync_status(
    migration_id: str = Path(...),
    caller: CallerIdentity = Depends(get_caller_identity),
    service: MigrationService = Depends(get_migration_service),
):
    """Check whether the source repo has changed since the last successful migration.

    Returns:
      { up_to_date: bool, current_sha, last_sha, summary, diff? }

    Use this to decide whether to trigger a Path 2 (incremental) re-run.
    If up_to_date=False, create a new migration — the pipeline will detect
    the changes automatically via detect_changes_node and run in incremental mode.
    """
    try:
        migration = await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)

        from configuration.database import async_session
        from services.change_detector import get_sync_status as _get_sync_status

        github_token = migration.github_token or ""
        async with async_session() as db:
            status_result = await _get_sync_status(migration_id, github_token, db)
        return status_result
    except MigrationDoesNotExist as e:
        raise NotFoundError(detail=str(e))
    except Exception as e:
        raise InternalServerError(detail=str(e))


# ── Cleanup endpoint ──────────────────────────────────────────────────────────

@router.post(
    "/{migration_id}/cleanup",
    status_code=status.HTTP_200_OK,
    summary="Flag or run cleanup for an interrupted deployment",
)
async def cleanup_migration(
    request: Request,
    migration_id: str = Path(...),
    destroy: bool = Query(False, description="If true, run terraform destroy (ADMIN ONLY — irreversible)"),
    confirm: str = Query("", description="Required when destroy=true: pass 'DESTROY' to confirm"),
    caller: CallerIdentity = Depends(get_caller_identity),
    db: AsyncSession = Depends(get_db_session),
    service: MigrationService = Depends(get_migration_service),
):
    """Handle a migration interrupted during terraform apply.

    - destroy=false (default): marks migration as 'cleanup_required' for human review.
    - destroy=true: runs terraform destroy (ADMIN JWT required + ?confirm=DESTROY).
    """
    from executor.cleanup import mark_cleanup_required, run_terraform_destroy
    from services.audit_service import audit

    try:
        migration = await service.get_by_id_for_user(migration_id, caller.user_id, caller.is_admin)
    except MigrationDoesNotExist:
        raise NotFoundError(detail=f"Migration {migration_id} not found")

    if not destroy:
        await mark_cleanup_required(migration_id, reason="Manual cleanup requested via API")
        await audit(db, action="migration.cleanup.flagged", request=request,
                    resource_type="migration", resource_id=migration_id,
                    resource_name=migration.repo_url)
        return {"status": "cleanup_required", "message": "Migration flagged for cleanup. Review cloud portal for partial resources."}

    # destroy=true — require admin JWT + explicit confirmation token
    from fastapi import HTTPException as _HTTPException
    from api.auth.jwt_handler import get_current_user, TokenData
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from data.models.user_model import UserRole

    _bearer = HTTPBearer(auto_error=False)
    credentials = await _bearer(request)
    if credentials is None:
        raise _HTTPException(
            status_code=401,
            detail="Admin JWT required to run terraform destroy",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from api.auth.jwt_handler import decode_token
    payload = decode_token(credentials.credentials, expected_type="access")
    if payload.get("role") != UserRole.ADMIN.value:
        raise _HTTPException(status_code=403, detail="Only admins can trigger terraform destroy")

    if confirm != "DESTROY":
        raise _HTTPException(
            status_code=422,
            detail="Pass ?confirm=DESTROY to confirm this irreversible action",
        )

    manifest = (migration.artifacts or {}).get("deployment_manifest") or {}
    work_dir  = manifest.get("work_dir")
    result = run_terraform_destroy(migration_id, work_dir=work_dir)

    await audit(db, action="migration.cleanup.destroy", request=request,
                resource_type="migration", resource_id=migration_id,
                resource_name=migration.repo_url,
                details={"work_dir": work_dir, "success": result["success"],
                         "admin_id": payload.get("sub")},
                success=result["success"],
                error=result.get("error"))

    if result["success"]:
        await service.update_by_id(migration_id, {"deployment_status": "destroyed"})

    return {
        "success":  result["success"],
        "output":   result["output"],
        "error":    result.get("error"),
        "message":  "Resources destroyed." if result["success"] else "Destroy failed — check output.",
    }
