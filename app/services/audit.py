"""Write structured, secret-free audit events inside the caller's transaction."""
from ..db import AuditLog, now, uid


def audit(db, tenant_id: str, action: str, target: str | None = None,
          details: dict | None = None, actor_user_id: str | None = None,
          actor_type: str = 'user') -> None:
    """Never pass tokens, passwords, message contents or raw request bodies in details."""
    db.add(AuditLog(id=uid(), tenant_id=tenant_id, actor_user_id=actor_user_id,
                    actor_type=actor_type, action=action, target=target,
                    details=details or {}, created_at=now()))
