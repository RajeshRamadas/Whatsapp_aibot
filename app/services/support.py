"""Resolve support grants without borrowing a customer's identity or permissions."""
from datetime import timedelta

from sqlalchemy import select

from ..db import now
from ..models.support import SupportSession
from .audit import audit
from .auth import hash_token

COOKIE = 'chatbot_support'
DURATION = timedelta(minutes=15)


def current(db, request, session, user):
    token = request.cookies.get(COOKIE)
    if not token or not user.is_platform_admin:
        return None
    return db.scalar(select(SupportSession).where(SupportSession.token_hash == hash_token(token),
        SupportSession.user_session_id == session.id_hash, SupportSession.ended_at.is_(None),
        SupportSession.expires_at > now()))


def end(db, session_id, operator_id):
    grants = db.scalars(select(SupportSession).where(SupportSession.user_session_id == session_id,
        SupportSession.ended_at.is_(None), SupportSession.expires_at > now()).with_for_update())
    for grant in grants:
        grant.ended_at = now()
        audit(db, grant.tenant_id, 'support.ended', grant.id, actor_user_id=operator_id, actor_type='platform_admin')
