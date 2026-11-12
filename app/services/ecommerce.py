"""Tenant store controls and leased, atomic daily catalogue publication."""
import asyncio
import json
from datetime import timedelta

from sqlalchemy import delete, func, or_, select

from .. import llm
from ..config import settings
from ..db import Chunk, Session, Source, Tenant, now, uid
from ..integrations.ecommerce import Store
from ..integrations.http_actions.transport import ActionError
from ..models.ecommerce import StoreConnection as Connection
from . import crypto
from .audit import audit
from .usage import meter


def owned(db, tenant_id, identity, lock=False):
    stmt = select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == str(identity))
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if not row:
        raise ActionError('not_found', 404)
    return row


def view(row):
    return {key: getattr(row, key) for key in ('id', 'provider', 'label', 'origin', 'enabled', 'status',
        'product_count', 'last_success_at', 'last_error', 'next_sync')}


def connect(tenant_id, provider, label, origin, credentials):
    packet = crypto.encrypt(tenant_id, json.dumps(credentials).encode())
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        if db.scalar(select(func.count()).select_from(Connection).where(Connection.tenant_id == tenant_id)) >= 5:
            raise ActionError('connection_limit', 409)
        row = Connection(tenant_id=tenant_id, provider=provider, label=label, origin=origin, credentials_enc=packet)
        db.add(row)
        db.flush()
        audit(db, tenant_id, 'store.created', row.id, actor_type='admin')
        return view(row)


def store(row):
    return Store(row.provider, row.origin, json.loads(crypto.decrypt(row.tenant_id, row.credentials_enc)))


def test(tenant_id, identity):
    with Session() as db:
        row = owned(db, tenant_id, identity)
    error = None
    try:
        asyncio.run(store(row).test())
    except ActionError as exc:
        error = exc.code
    except Exception:
        error = 'invalid_store_response'
    with Session.begin() as db:
        current = owned(db, tenant_id, identity, True)
        current.status, current.last_error = ('needs_attention', error) if error else ('connected', None)
        if error:
            current.enabled, current.next_sync, current.lease = False, None, None
            if current.source_id and (source := db.get(Source, current.source_id)):
                source.status = 'paused'
        audit(db, tenant_id, 'store.tested', identity, actor_type='admin', details={'success': not bool(error)})
        return view(current)


def configure(tenant_id, identity, enabled):
    with Session.begin() as db:
        row = owned(db, tenant_id, identity, True)
        if enabled and row.status != 'connected':
            raise ActionError('test_required', 409)
        row.enabled, row.lease, row.lease_until = enabled, None, None
        row.next_sync = now() if enabled else None
        if row.source_id and (source := db.get(Source, row.source_id)):
            source.status = 'paused'
        audit(db, tenant_id, 'store.enabled' if enabled else 'store.paused', identity, actor_type='admin')
        return view(row)


def request_sync(tenant_id, identity):
    with Session.begin() as db:
        row = owned(db, tenant_id, identity, True)
        if not row.enabled:
            raise ActionError('store_paused', 409)
        if row.lease and row.lease_until and row.lease_until > now():
            raise ActionError('sync_running', 409)
        row.next_sync = now()
        return view(row)


def remove(tenant_id, identity):
    with Session.begin() as db:
        row = owned(db, tenant_id, identity, True)
        if row.source_id:
            db.execute(delete(Source).where(Source.tenant_id == tenant_id, Source.id == row.source_id))
        audit(db, tenant_id, 'store.deleted', identity, actor_type='admin')
        db.delete(row)


def pending():
    with Session() as db:
        return list(db.execute(select(Connection.tenant_id, Connection.id).join(Tenant).where(
            Connection.enabled, Tenant.suspended.is_(False), Connection.next_sync <= now(),
            or_(Connection.lease_until.is_(None), Connection.lease_until <= now())).limit(20)))


def sync(tenant_id, identity):
    with Session.begin() as db:
        row = owned(db, tenant_id, identity, True)
        if (not row.enabled or db.get(Tenant, tenant_id).suspended or
                row.lease and row.lease_until and row.lease_until > now()):
            return
        lease = row.lease = uid()
        row.lease_until = now() + timedelta(minutes=20)
    error, products, vectors = None, [], []
    try:
        async def collect():
            async with asyncio.timeout(120):
                return await store(row).products(settings.ecommerce_max_products)
        products = asyncio.run(collect())
        texts = [json.dumps(p, ensure_ascii=False) for p in products]
        if any(len(t) > 12000 for t in texts) or len({p['id'] for p in products}) != len(products):
            raise ActionError('invalid_store_response')
        with meter(tenant_id):
            for offset in range(0, len(texts), 32):
                vectors.extend(llm.embedder().embed(texts[offset:offset + 32]))
        if len(vectors) != len(texts):
            raise ActionError('embedding_failed')
    except ActionError as exc:
        error = exc.code
    except TimeoutError:
        error = 'timeout'
    except Exception:
        error = 'sync_failed'
    with Session.begin() as db:
        current = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id,
            Connection.id == identity).with_for_update())
        if (not current or not current.enabled or current.lease != lease or
                current.lease_until <= now() or db.get(Tenant, tenant_id).suspended):
            return
        current.lease, current.lease_until = None, None
        current.last_error = error
        current.next_sync = now() + timedelta(hours=1 if error else 24)
        source = db.scalar(select(Source).where(Source.tenant_id == tenant_id, Source.id == current.source_id))
        if error:
            if source:
                source.status = 'error'
            if error == 'auth_failed':
                current.enabled, current.status = False, 'needs_attention'
            return
        if not source:
            source = Source(tenant_id=tenant_id, filename=current.label, kind='ecommerce', file_path='', status='ready')
            db.add(source)
            db.flush()
            current.source_id = source.id
        else:
            db.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.source_id == source.id))
        for i, (product, content, vector) in enumerate(zip(products, texts, vectors, strict=True)):
            db.add(Chunk(tenant_id=tenant_id, source_id=source.id, position=i,
                         heading=product['title'], content=content, embedding=vector))
        source.status = 'ready'
        current.product_count, current.last_success_at = len(products), now()
        audit(db, tenant_id, 'store.catalogue_synced', identity, actor_type='system',
              details={'products': len(products)})
