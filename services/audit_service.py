"""
audit_service.py — Writes audit log entries.

Usage in any router:
    from services.audit_service import audit
    await audit(db, action="migration.created", user=current_user,
                resource_type="migration", resource_id=m.id,
                resource_name=m.repo_url, request=request)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.audit_log_model import AuditLog

logger = logging.getLogger("AuditService")


async def audit(
    db: AsyncSession,
    *,
    action: str,
    user=None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    details: Optional[dict] = None,
    request=None,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Write one audit log entry. Non-blocking — errors are logged but never raised."""
    try:
        entry = AuditLog(
            user_id       = str(user.id)   if user and hasattr(user, "id")   else None,
            username      = user.username   if user and hasattr(user, "username") else None,
            user_role     = str(user.role)  if user and hasattr(user, "role")  else None,
            ip_address    = _get_ip(request),
            action        = action,
            resource_type = resource_type,
            resource_id   = str(resource_id) if resource_id else None,
            resource_name = str(resource_name)[:200] if resource_name else None,
            details       = details,
            method        = request.method if request else None,
            path          = str(request.url.path) if request else None,
            success       = success,
            error         = str(error)[:500] if error else None,
        )
        db.add(entry)
        await db.commit()
    except Exception as exc:
        logger.warning("AuditService: failed to write audit log — %s", exc)


def _get_ip(request) -> Optional[str]:
    if not request:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if hasattr(request, "client") and request.client:
        return request.client.host
    return None
