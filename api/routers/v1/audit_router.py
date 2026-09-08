"""
audit_router.py — Read-only audit log endpoint (admin only).

GET /api/v1/audit/logs?limit=50&skip=0&action=&user_id=&resource_id=
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime

from api.auth.jwt_handler import require_admin
from configuration.database import get_db_session
from data.models.audit_log_model import AuditLog

router = APIRouter(prefix="/audit", tags=["AUDIT"])


class AuditLogResponse(BaseModel):
    id:            str
    user_id:       Optional[str]
    username:      Optional[str]
    user_role:     Optional[str]
    ip_address:    Optional[str]
    action:        str
    resource_type: Optional[str]
    resource_id:   Optional[str]
    resource_name: Optional[str]
    details:       Optional[dict]
    method:        Optional[str]
    path:          Optional[str]
    status_code:   Optional[int]
    success:       bool
    error:         Optional[str]
    created_at:    datetime

    class Config:
        from_attributes = True


@router.get(
    "/logs",
    response_model=List[AuditLogResponse],
    summary="List audit logs (admin only)",
    dependencies=[Depends(require_admin)],
)
async def list_audit_logs(
    skip:        int           = Query(0, ge=0),
    limit:       int           = Query(50, ge=1, le=500),
    action:      Optional[str] = Query(None, description="Filter by action prefix, e.g. 'migration'"),
    user_id:     Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    success:     Optional[bool]= Query(None),
    db: AsyncSession           = Depends(get_db_session),
):
    """Returns audit log entries ordered newest first. Admin only."""
    stmt = select(AuditLog).order_by(desc(AuditLog.created_at))

    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if success is not None:
        stmt = stmt.where(AuditLog.success == success)

    stmt = stmt.offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
