"""Durable refresh, access fences, and per-document atomic knowledge publication."""
import hashlib
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, exists, or_, select

from .. import ingest, llm
from ..db import Chunk, Session, Source, Tenant, now, uid
from ..integrations.knowledge_sync.http import DEADLINE, SyncError
from ..integrations.knowledge_sync.providers import Drive, Notion
from ..models.knowledge_sync import KnowledgeConnection as Connection
from ..spreadsheets import parse, store
from . import knowledge_connections as connections
from .usage import meter


def usable():
    return or_(Source.sync_connection_id.is_(None), exists(select(Connection.id).where(
        Connection.id == Source.sync_connection_id, Connection.tenant_id == Source.tenant_id,
        Connection.enabled, Connection.status == 'connected', Connection.last_success > now() - timedelta(days=1))))


def fenced(db, snapshot):
    # All mutations take tenant then connection locks, matching spreadsheet publication/deletion.
    db.scalar(select(Tenant).where(Tenant.id == snapshot.tenant_id).with_for_update())
    row = connections.owned(db, snapshot.tenant_id, snapshot.id, True)
    if not row.enabled or row.lease != snapshot.lease or db.get(Tenant, row.tenant_id).suspended:
        raise SyncError('stale_run')
    return row


def remove(db, tenant_id, source):
    store.remove(db, tenant_id, source.id)
    db.delete(source)


def publish(snapshot, item, content):
    if len(content) > 10_000_000:
        raise SyncError('document_limit')
    checksum = hashlib.sha256(content).hexdigest()
    with Session.begin() as db:
        fenced(db, snapshot)
        previous = db.scalar(select(Source).where(Source.tenant_id == snapshot.tenant_id,
            Source.sync_connection_id == snapshot.id, Source.sync_external_id == item['id']))
        if (previous and previous.checksum == checksum and previous.kind == item['format']
                and previous.filename == item['name']):
            previous.status, previous.error, previous.sync_version = 'ready', None, item['version']
            previous.filename, previous.url = item['name'], item['url']
            return
    with tempfile.TemporaryDirectory(prefix='knowledge-sync-') as directory:
        path = Path(directory) / ('source.' + item['format'])
        path.write_bytes(content)
        sheets = parse.parse(path) if item['format'] in ('csv', 'xlsx') else []
        pieces = [] if sheets else ingest.chunk_blocks(ingest.extract(path, path.suffix))
    if not sheets and not pieces or len(pieces) > 2000:
        raise SyncError('document_limit')
    vectors = []
    with meter(snapshot.tenant_id):
        for offset in range(0, len(pieces), ingest.EMBED_BATCH):
            vectors.extend(llm.embedder().embed([ingest.embedding_text(item['name'], p)
                                                for p in pieces[offset:offset + ingest.EMBED_BATCH]]))
    with Session.begin() as db:
        fenced(db, snapshot)
        source = db.scalar(select(Source).where(Source.tenant_id == snapshot.tenant_id,
            Source.sync_connection_id == snapshot.id, Source.sync_external_id == item['id']).with_for_update())
        if not source:
            source = Source(id=uid(), tenant_id=snapshot.tenant_id, sync_connection_id=snapshot.id,
                            sync_external_id=item['id'], file_path='sync:', kind=item['format'], filename=item['name'])
            db.add(source)
            db.flush()
        source.filename, source.kind, source.url = item['name'], item['format'], item['url']
        source.sync_version, source.checksum = item['version'], hashlib.sha256(content).hexdigest()
        db.execute(delete(Chunk).where(Chunk.tenant_id == snapshot.tenant_id, Chunk.source_id == source.id))
        store.remove(db, snapshot.tenant_id, source.id)
        if sheets:
            store.publish(db, snapshot.tenant_id, source.id, sheets)  # Changed sheets require explicit re-enablement.
        else:
            for n, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
                db.add(Chunk(tenant_id=snapshot.tenant_id, source_id=source.id, position=n,
                             heading=piece.heading, page=piece.page, content=piece.content, embedding=vector))
        source.status, source.error = 'ready', None


def execute(snapshot):
    provider = Drive if snapshot.provider == 'drive' else Notion
    client = provider(connections.access(snapshot))
    items, cursor, changed = client.snapshot(snapshot.selection, snapshot.cursor)
    by_id = {i['id']: i for i in items}
    # Only a complete metadata scan authorizes removals. A page of partial results never does.
    with Session.begin() as db:
        fenced(db, snapshot)
        sources = db.scalars(select(Source).where(Source.tenant_id == snapshot.tenant_id,
                                                Source.sync_connection_id == snapshot.id)).all()
        versions = {s.sync_external_id: s.sync_version for s in sources if s.status == 'ready'}
        for source in sources:
            if source.sync_external_id not in by_id:
                remove(db, snapshot.tenant_id, source)
            elif (versions.get(source.sync_external_id) != by_id[source.sync_external_id]['version']
                    or changed is None or source.sync_external_id in changed):
                source.status = 'processing'  # Old changed content cannot answer while extraction is pending.
    failed = False
    for item in items:
        if versions.get(item['id']) == item['version'] and changed is not None and item['id'] not in changed:
            continue
        # Notion polls full content too: edits to child blocks may not update the parent page timestamp.
        try:
            publish(snapshot, item, client.content(item))
        except SyncError as exc:
            if exc.code in ('stale_run', 'sync_deadline'):
                raise
            failed = True
            with Session.begin() as db:
                fenced(db, snapshot)
                source = db.scalar(select(Source).where(Source.tenant_id == snapshot.tenant_id,
                    Source.sync_connection_id == snapshot.id, Source.sync_external_id == item['id']))
                if source:
                    remove(db, snapshot.tenant_id, source)  # Permission/content failure never retains an old answer.
        except Exception:
            raise SyncError('document_failed') from None
    if failed:
        raise SyncError('document_failed')
    return cursor


def process_one():
    with Session.begin() as db:
        for expired in db.scalars(select(Connection).where(Connection.status == 'requested',
                Connection.oauth_expires_at <= now()).with_for_update(skip_locked=True)):
            expired.status, expired.last_error = 'needs_attention', 'authorization_required'
            expired.oauth_state_hash, expired.oauth_secret_enc = None, None
        row = db.scalar(select(Connection).join(Tenant, Tenant.id == Connection.tenant_id).where(
            Connection.enabled, Connection.status.in_(['connected', 'connecting']), ~Tenant.suspended,
            Connection.next_run <= now()).order_by(Connection.next_run)
            .with_for_update(of=Connection, skip_locked=True).limit(1))
        if not row:
            return False
        row.lease, row.next_run, row.attempts = uid(), now() + timedelta(minutes=15), row.attempts + 1
    error, cursor = None, row.cursor
    token = DEADLINE.set(time.monotonic() + 240)
    try:
        cursor = execute(row)
    except SyncError as exc:
        error = exc
    except Exception:
        error = SyncError('sync_failed')
    finally:
        DEADLINE.reset(token)
    try:
        with Session.begin() as db:
            current = fenced(db, row)
            current.lease = None
            if error:
                current.last_error = error.code
                current.next_run = now() + timedelta(seconds=max(error.wait, min(3600, 30 * 2 ** min(row.attempts, 7))))
                if error.code == 'authorization_required':
                    current.status, current.oauth_secret_enc = 'needs_attention', None
                    connections.purge(db, current)
            else:
                current.cursor, current.status = cursor, 'connected'
                current.last_success, current.last_error = now(), None
                current.attempts, current.next_run = 0, now() + timedelta(minutes=15)
    except SyncError:
        pass  # Removed, paused or changed while IO was in flight; no stale result is published.
    return True
