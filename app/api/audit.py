"""Paginated workspace audit events, visible only to admins and owners."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from ..db import AuditLog, Session
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1', tags=['audit'])


@router.get('/audit-log')
def list_audit_log(principal: Principal = Depends(require_role('admin')),
                   limit: int = Query(default=50, ge=1, le=100),
                   offset: int = Query(default=0, ge=0)):
    with Session() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.tenant_id == principal.tenant.id)
                          .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                          .limit(limit).offset(offset)).all()
    return [{'id': row.id, 'action': row.action, 'target': row.target,
             'actor_user_id': row.actor_user_id, 'actor_type': row.actor_type,
             'details': row.details, 'created_at': row.created_at} for row in rows]
