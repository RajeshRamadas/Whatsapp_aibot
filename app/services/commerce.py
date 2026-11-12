"""Store configuration and verified-customer order access, without sessions across IO."""
import asyncio
import json

from sqlalchemy import delete, select

from ..db import Conversation, Session, Source, Tenant, now
from ..integrations.commerce import Provider
from ..integrations.http_actions.transport import ActionError
from ..models.commerce import Store
from . import crypto
from .audit import audit
from .http_actions import phone


def scoped(db, tenant_id, identity, *, lock=False):
    query = select(Store).where(Store.tenant_id == tenant_id, Store.id == identity)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise ActionError('not_found', 404)
    return row


def view(row):
    return {key: getattr(row, key) for key in ('id', 'provider', 'url', 'status', 'source_id', 'product_count',
                                              'last_success', 'last_error', 'next_run', 'lease')}


def listing(tenant_id):
    with Session() as db:
        return [view(s) for s in db.scalars(select(Store).where(Store.tenant_id == tenant_id))]


def connect(tenant_id, values):
    private = crypto.encrypt(tenant_id, json.dumps({'token': values.token.get_secret_value(),
                                                  'consumer_key': values.consumer_key}).encode())
    with Session.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if db.scalar(select(Store.id).where(Store.tenant_id == tenant_id)):
            raise ActionError('disconnect_existing_store', 409)
        row = Store(tenant_id=tenant_id, provider=values.provider, url=values.url, credentials_enc=private)
        db.add(row)
        db.flush()
        audit(db, tenant_id, 'commerce.connected', row.id, actor_type='admin')
        return view(row)


def provider(row):
    return Provider(row, json.loads(crypto.decrypt(row.tenant_id, row.credentials_enc)))


def test_connection(tenant_id, identity):
    with Session() as db:
        row = scoped(db, tenant_id, identity)
    error = None
    try:
        asyncio.run(provider(row).test())
    except (ActionError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        error = exc.code if isinstance(exc, ActionError) else 'invalid_response'
    with Session.begin() as db:
        row = scoped(db, tenant_id, identity, lock=True)
        if error:
            row.status, row.last_error, row.lease, row.next_run = 'needs_test', error, None, None
        else:
            row.status, row.last_error = 'connected', None
            row.next_run = now()
        return view(row)


def disconnect(tenant_id, identity):
    with Session.begin() as db:
        row = scoped(db, tenant_id, identity, lock=True)
        if row.source_id:
            db.execute(delete(Source).where(Source.tenant_id == tenant_id, Source.id == row.source_id))
        db.delete(row)
        audit(db, tenant_id, 'commerce.disconnected', identity, actor_type='admin')


def lookup(tenant_id, identity, conversation_id, number, claimed_phone):
    # A model-supplied phone is only a consistency check; identity comes from the inbound channel.
    with Session() as db:
        store = scoped(db, tenant_id, identity)
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant_id,
                                                    Conversation.id == conversation_id))
        verified = phone(conv.external_user) if conv and conv.channel == 'whatsapp' else None
        if (not verified or phone(claimed_phone) != verified or conv.status != 'bot' or conv.handoff
                or store.status != 'connected' or db.get(Tenant, tenant_id).suspended):
            return None
    result = asyncio.run(provider(store).order(number))
    if not result or not any(phone(p or '') == verified for p in result.pop('phones')):
        return None  # Absent order and wrong owner have identical responses.
    result.pop('email', None)
    with Session() as db:
        current = db.scalar(select(Store.id).where(Store.tenant_id == tenant_id, Store.id == identity,
                                                  Store.status == 'connected'))
        conv = db.scalar(select(Conversation).where(Conversation.tenant_id == tenant_id,
                                                   Conversation.id == conversation_id))
        if not current or not conv or conv.handoff or conv.status != 'bot':
            return None
    return result
