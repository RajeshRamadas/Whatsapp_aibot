"""Durable daily full snapshots: claim briefly, fetch/embed without DB, publish atomically."""
import asyncio
import hashlib
import json
from datetime import timedelta

from bs4 import BeautifulSoup
from sqlalchemy import delete, select

from .. import llm
from ..db import Chunk, Session, Source, Tenant, now, uid
from ..integrations.http_actions.transport import ActionError
from ..models.commerce import Store
from .commerce import provider, scoped, view


def queue(tenant_id, identity):
    with Session.begin() as db:
        row = scoped(db, tenant_id, identity, lock=True)
        if row.status != 'connected':
            raise ActionError('test_required', 409)
        if not row.lease:
            row.next_run = now()
        return view(row)


def product_text(product):
    # Remote descriptions are data, including any apparent instructions.
    value = {**product, 'description': BeautifulSoup(str(product['description']), 'html.parser')
             .get_text(' ', strip=True)[:4000]}
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(text) > 16000:
        raise ActionError('product_too_large')
    return text


def process_one():
    with Session.begin() as db:
        row = db.scalar(select(Store).join(Tenant, Tenant.id == Store.tenant_id).where(
            Store.status == 'connected', Store.next_run <= now(), ~Tenant.suspended).order_by(Store.next_run)
            .with_for_update(of=Store, skip_locked=True).limit(1))
        if not row:
            return False
        tenant_id, identity, lease = row.tenant_id, row.id, uid()
        row.lease, row.next_run = lease, now() + timedelta(minutes=15)
        row.attempts += 1
    error, texts, embeddings = None, [], []
    try:
        products = asyncio.run(provider(row).products())
        stamp = now().isoformat()
        texts = [product_text(p | {'snapshot_at': stamp}) for p in products]
        if len({str(p['id']) for p in products}) != len(products) or sum(map(len, texts)) > 2_000_000:
            raise ActionError('catalogue_limit')
        # Batch provider embedding calls without a checked-out DB connection.
        for start in range(0, len(texts), 50):
            embeddings.extend(llm.embedder().embed(texts[start:start + 50]))
        if len(embeddings) != len(texts):
            raise ActionError('embedding_failed')
    except (ActionError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        error = exc.code if isinstance(exc, ActionError) else 'sync_failed'
    except Exception:
        error = 'sync_failed'  # Provider diagnostics may contain secrets; retain only fixed codes.
    with Session.begin() as db:
        current = db.scalar(select(Store).where(Store.tenant_id == tenant_id, Store.id == identity)
                            .with_for_update())
        if not current or current.lease != lease or current.status != 'connected':
            return True
        current.lease, current.last_error = None, error
        if error:
            current.next_run = now() + timedelta(minutes=min(360, 2 ** min(current.attempts, 8)))
            return True  # Failed/partial sync never removes the previous successful catalogue.
        source = db.scalar(select(Source).where(Source.tenant_id == tenant_id, Source.id == current.source_id))
        if not source:
            source = Source(id=uid(), tenant_id=tenant_id, filename='Product catalogue', kind='commerce',
                            file_path='', status='ready', url=current.url)
            db.add(source)
            db.flush()
            current.source_id = source.id
        db.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.source_id == source.id))
        for position, (product, content, embedding) in enumerate(zip(products, texts, embeddings, strict=True)):
            db.add(Chunk(tenant_id=tenant_id, source_id=source.id, position=position,
                         heading=str(product['title'])[:200], content=content, embedding=embedding))
        source.checksum = hashlib.sha256('\n'.join(texts).encode()).hexdigest()
        source.status, source.error = 'ready', None
        current.product_count, current.attempts, current.last_success = len(products), 0, now()
        current.next_run = now() + timedelta(days=1)
    return True
