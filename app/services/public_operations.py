"""Claim durable operations, release DB sessions, then perform storage/model work."""
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select

from .. import ingest, llm
from ..db import QAPair, Session, Source, Tenant, now
from ..models.public_api import ApiOperation, ApiRequest
from .events import publish_sync
from .storage import storage
from .usage import meter


def scoped(db, model, tenant_id, identity, lock=False):
    query = select(model).where(model.tenant_id == tenant_id, model.id == identity)
    return db.scalar(query.with_for_update() if lock else query)


def process_one():
    with Session.begin() as db:
        op = db.scalar(select(ApiOperation).where(or_(
            and_(ApiOperation.state == 'queued', ApiOperation.next_attempt_at <= now()),
            and_(ApiOperation.state == 'running', ApiOperation.claimed_at < now() - timedelta(minutes=30))))
            .order_by(ApiOperation.created_at, ApiOperation.id).with_for_update(skip_locked=True).limit(1))
        if not op:
            return False
        tenant_id, identity, resource_id, kind, payload = op.tenant_id, op.id, op.resource_id, op.kind, op.payload
        tenant = db.get(Tenant, tenant_id)
        if not tenant or tenant.suspended:
            op.state, op.last_error, op.payload = 'failed', 'workspace_unavailable', {}
            return True
        op.state, op.claimed_at, op.attempts = 'running', now(), op.attempts + 1
        stamp = op.claimed_at
    error, vector = None, None
    try:
        if kind == 'source.create':
            storage().put(payload['storage_key'], payload['content'].encode())
            ingest.ingest(resource_id, tenant_id)
        elif kind == 'source.delete':
            storage().delete(payload['storage_key'])
        elif kind in ('qa.create', 'qa.update'):
            with meter(tenant_id):
                vector = llm.embedder().embed([payload['question']])[0]
        else:
            raise ValueError('Unsupported operation')
    except Exception:
        error = 'knowledge_processing_failed'
    with Session.begin() as db:
        op = scoped(db, ApiOperation, tenant_id, identity, lock=True)
        if not op or op.state != 'running' or op.claimed_at != stamp:
            return True
        if not error and kind.startswith('qa.'):
            row = scoped(db, QAPair, tenant_id, resource_id, lock=True)
            if row is None and kind == 'qa.create':
                row = QAPair(id=resource_id, tenant_id=tenant_id, **payload, embedding=vector)
                db.add(row)
            elif row is not None:
                row.question, row.answer, row.embedding = payload['question'], payload['answer'], vector
            else:
                error = 'resource_deleted'
        op.last_error = error
        op.state = 'queued' if error and op.attempts < 3 else 'failed' if error else 'complete'
        op.next_attempt_at = now() + timedelta(seconds=60 * op.attempts)
        if op.state in ('complete', 'failed'):
            op.payload = {}
        if kind == 'source.create' and error:
            row = scoped(db, Source, tenant_id, resource_id, lock=True)
            if row:
                row.status, row.error = ('failed' if op.state == 'failed' else 'queued'), error
    if kind.startswith('source.'):
        publish_sync(tenant_id, 'source.updated', {'source_id': resource_id})
    return True


def expire():
    with Session.begin() as db:
        db.execute(delete(ApiRequest).where(ApiRequest.expires_at < now()))
        db.execute(delete(ApiOperation).where(ApiOperation.state.in_(['complete', 'failed']),
                                              ApiOperation.created_at < now() - timedelta(days=30)))
