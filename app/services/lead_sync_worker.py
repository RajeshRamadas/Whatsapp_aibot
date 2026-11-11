"""Durable connector discovery and lead deliveries, with revision fencing and bounded retries."""
import json
import random
from datetime import timedelta

from sqlalchemy import and_, or_, select

from ..db import Membership, Session, Tenant, User, now
from ..integrations.lead_sync.http import SyncError
from ..models.lead_sync import LeadConnection as Connection
from ..models.lead_sync import LeadDelivery as Delivery
from . import crypto, email, lead_oauth
from .appointments import AppointmentError, row


def client(tenant_id, connection):
    if connection.provider == 'sheets':
        from ..integrations.lead_sync.sheets import Sheets
        return Sheets(tenant_id, lead_oauth.access(tenant_id, connection.id, connection.revision))
    from ..integrations.lead_sync.crm import CRM
    credentials = json.loads(crypto.decrypt(tenant_id, connection.credentials_enc))
    return CRM(tenant_id, connection, credentials)


def attention(db, connection, code):
    callbacks = []
    if connection.status != 'needs_attention':
        for user in db.scalars(select(User).join(Membership, Membership.user_id == User.id).where(
                Membership.tenant_id == connection.tenant_id, Membership.role.in_(['owner', 'admin']),
                User.email_verified_at.is_not(None))):
            callbacks.append(email.sender.stage(db, user.email, 'Lead sync needs attention',
                'Reconnect or test the account from Lead sync. Leads remain saved in the workspace.'))
    connection.status, connection.last_error = 'needs_attention', code
    return callbacks


def process_one():
    with Session.begin() as db:
        job = db.scalar(select(Delivery).where(or_(and_(Delivery.state == 'queued', Delivery.next_attempt_at <= now()),
            and_(Delivery.state == 'working', Delivery.claimed_at < now() - timedelta(minutes=10))))
            .order_by(Delivery.next_attempt_at, Delivery.created_at, Delivery.id)
            .with_for_update(skip_locked=True).limit(1))
        if not job:
            return False
        tenant_id, identity, connection_id, revision = job.tenant_id, job.id, job.connection_id, job.revision
        connection = row(db, Connection, tenant_id, connection_id, lock=True)
        if (connection.revision != revision or connection.status == 'disconnected'
                or job.kind == 'sync' and (not connection.enabled or not job.conversation_id)):
            job.state, job.payload, job.last_error = 'skipped', {}, 'connection_changed'
            return True
        if db.get(Tenant, tenant_id).suspended or job.kind == 'sync' and connection.status != 'connected':
            job.state, job.next_attempt_at = 'queued', now() + timedelta(minutes=1)
            return True
        if connection.lease_until and connection.lease_until > now():
            job.state, job.next_attempt_at = 'queued', connection.lease_until
            return True
        if job.kind == 'sync' and connection.provider != 'sheets' and db.scalar(select(Delivery.id).where(
            Delivery.tenant_id == tenant_id, Delivery.connection_id == connection_id,
            Delivery.conversation_id == job.conversation_id, Delivery.lead_version > job.lead_version,
            Delivery.kind == 'sync', Delivery.state == 'complete').limit(1)):
            job.state, job.payload, job.last_error = 'skipped', {}, 'superseded'
            return True
        stamp = now()
        connection.lease_until = stamp + timedelta(minutes=10)
        job.state, job.claimed_at, job.attempts = 'working', stamp, job.attempts + 1
        if job.kind == 'oauth' and connection.credentials_enc and not connection.oauth_secret_enc:
            kind = 'discover'
        else:
            kind = job.kind
        db.flush()
        db.expunge(job)
        db.expunge(connection)

    def checkpoint(**values):
        with Session.begin() as db:
            current = row(db, Delivery, tenant_id, identity, lock=True)
            c = row(db, Connection, tenant_id, connection_id, lock=True)
            if (current.state != 'working' or current.claimed_at != stamp or c.revision != revision
                    or c.status == 'disconnected' or kind == 'sync' and (not c.enabled or not current.conversation_id)):
                raise SyncError('connection_changed')
            for key, value in values.items():
                if key not in ('submitted', 'note_submitted', 'remote_id', 'contact_done'):
                    raise ValueError('Invalid delivery checkpoint')
                setattr(current, key, value)
                setattr(job, key, value)

    error, catalog = None, None
    try:
        if kind == 'oauth':
            lead_oauth.exchange(tenant_id, connection_id, revision)
        adapter = client(tenant_id, connection)
        if kind in ('discover', 'oauth'):
            catalog = adapter.inventory(job.payload.get('spreadsheet_id'), connection.catalog)
        elif kind == 'create_sheet':
            catalog = adapter.create_sheet(job, checkpoint, connection.catalog)
        elif kind == 'sync':
            adapter.deliver(job, connection.config, checkpoint)
        else:
            raise SyncError('invalid_operation')
    except SyncError as exc:
        error = exc
    except AppointmentError:
        error = SyncError('connection_changed')
    except Exception:
        error = SyncError('operation_failed')  # Never persist provider bodies, secrets or customer content.
    callbacks = []
    with Session.begin() as db:
        current = db.scalar(select(Delivery).where(Delivery.id == identity, Delivery.tenant_id == tenant_id)
                            .with_for_update())
        c = db.scalar(select(Connection).where(Connection.id == connection_id, Connection.tenant_id == tenant_id)
                      .with_for_update())
        if not current or not c or current.claimed_at != stamp:
            return True
        if c.lease_until == stamp + timedelta(minutes=10):
            c.lease_until = None
        if c.revision != revision or c.status == 'disconnected' or error and error.code == 'connection_changed':
            current.state, current.payload, current.last_error = 'skipped', {}, 'connection_changed'
            return True
        if error:
            retry = error.retry and current.attempts < 5
            current.state, current.last_error, c.last_error = 'queued' if retry else 'failed', error.code, error.code
            current.next_attempt_at = now() + timedelta(seconds=max(error.wait,
                30 * 2 ** (current.attempts - 1) + random.uniform(0, 5)))
            if not retry and (error.code == 'credentials_required' or kind == 'oauth'):
                callbacks = attention(db, c, error.code)
                c.oauth_secret_enc = None
        else:
            current.state, current.payload, current.last_error = 'complete', {}, None
            c.last_success_at, c.last_error = now(), None
            if catalog is not None:
                c.catalog, c.status = catalog, 'connected'
    for callback in callbacks:
        callback()
    return True
