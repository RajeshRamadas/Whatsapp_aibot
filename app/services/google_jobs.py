"""Durable operation claims, connection leases, bounded retries and revision fencing."""
from datetime import timedelta

from sqlalchemy import and_, or_, select

from ..db import Session, Tenant, now
from ..integrations.google_calendar import CalendarError
from ..models.google_calendar import GoogleConnection as Connection
from ..models.google_calendar import GoogleTask as Task
from . import google_calendar


def process_one():
    with Session.begin() as db:
        task = db.scalar(select(Task).where(Task.kind.not_in(['booking', 'calendar_cancel']),
            or_(and_(Task.state == 'queued', Task.next_attempt_at <= now()),
            and_(Task.state == 'working', Task.claimed_at < now() - timedelta(minutes=3))))
            .order_by(Task.next_attempt_at, Task.id).with_for_update(skip_locked=True).limit(1))
        if not task:
            return False
        tenant_id, task_id, identity, revision, kind = (
            task.tenant_id, task.id, task.connection_id, task.revision, task.kind)
        c = db.scalar(select(Connection).where(Connection.id == identity, Connection.tenant_id == tenant_id)
                      .with_for_update())
        if not c or c.revision != revision or db.get(Tenant, tenant_id).suspended:
            task.state = 'complete'
            return True
        if c.token_lease_until and c.token_lease_until > now():
            task.next_attempt_at = c.token_lease_until
            return True
        stamp = now()
        c.token_lease_until = stamp + timedelta(minutes=3)
        task.state, task.claimed_at, task.attempts = 'working', stamp, task.attempts + 1
        # A catalog failure after exchanging a code retries catalog lookup, never the exchange.
        if kind == 'connect' and c.credentials_enc and not c.oauth_secret_enc:
            kind = 'refresh'
    callbacks = []
    error, result = None, {}
    try:
        result = google_calendar.execute(tenant_id, identity, kind, revision)
    except CalendarError as exc:
        error = exc
    except Exception:
        error = CalendarError('operation_failed')  # Never persist/log raw provider or crypto exceptions.
    with Session.begin() as db:
        task = db.scalar(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id).with_for_update())
        c = db.scalar(select(Connection).where(Connection.id == identity, Connection.tenant_id == tenant_id)
                      .with_for_update())
        if not task or task.claimed_at != stamp or not c:
            return True
        if c.token_lease_until == stamp + timedelta(minutes=3):
            c.token_lease_until = None
        if c.revision != revision:
            task.state = 'complete'
            return True
        if error:
            retry = error.retry and task.attempts < 5
            task.state, task.last_error, c.last_error = 'queued' if retry else 'failed', error.code, error.code
            task.next_attempt_at = now() + timedelta(seconds=max(error.wait, 30 * 2 ** (task.attempts - 1)))
            if not retry:
                callbacks = google_calendar.attention(db, c, error.code)
                c.oauth_secret_enc = None
                if kind == 'disconnect':
                    c.status, c.credentials_enc, c.calendars = 'disconnected', None, []
        else:
            for key, value in result.items():
                setattr(c, key, value)
            c.last_success_at, c.last_error, task.state = now(), None, 'complete'
    for callback in callbacks:
        callback()
    return True
