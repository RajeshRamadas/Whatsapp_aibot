"""arq worker entry point: python -m app.jobs.worker."""
import asyncio

from arq import Retry, run_worker
from arq.connections import RedisSettings
from sqlalchemy import select, text

from .. import ingest, whatsapp
from ..config import settings
from ..db import AuditLog, Conversation, InboundEvent, Message, Session, Source, Tenant


async def sample_job(ctx: dict, tenant_id: str, job_id: str) -> str:
    """Persist a tenant-scoped audit event to prove a queued job actually ran."""
    with Session.begin() as db:
        if not db.get(Tenant, tenant_id):
            raise ValueError('Unknown workspace')
        db.add(AuditLog(tenant_id=tenant_id, actor_type='system', action='jobs.sample.completed',
                        target=job_id, details={}))
    return job_id


async def ingest_source(ctx: dict, source_id: str, tenant_id: str) -> None:
    """Process an uploaded source; retry transient failures, fail on final attempt."""
    with Session() as db:
        source = db.get(Source, source_id)
        if not source or source.tenant_id != tenant_id:
            return  # Deleted source or stale/cross-tenant job.
        if source.status == 'ready':
            return
    try:
        await asyncio.to_thread(ingest.ingest, source_id)
    except Exception as exc:
        with Session.begin() as db:
            source = db.get(Source, source_id)
            if source and source.tenant_id == tenant_id:
                if isinstance(exc, ValueError) or ctx.get('job_try', 1) >= WorkerSettings.max_tries:
                    source.status, source.error = 'failed', str(exc)[:500]
                else:
                    source.status = 'queued'
        if not isinstance(exc, ValueError) and ctx.get('job_try', 1) < WorkerSettings.max_tries:
            raise


async def handle_message(ctx: dict, event_id: str) -> None:
    """Debounce one contact and serialize its turn with a PostgreSQL advisory lock."""
    await asyncio.sleep(2)
    with Session.begin() as db:
        event = db.get(InboundEvent, event_id)
        if not event or event.status in ('processed', 'merged', 'failed'):
            return
        payload = event.payload
        contact = payload['from']
        tenant_id = event.tenant_id
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                   {'key': f'{tenant_id}:whatsapp:{contact}'})
        db.refresh(event)
        if event.status in ('processed', 'merged', 'failed'):
            return
        candidates = db.scalars(select(InboundEvent).where(
            InboundEvent.tenant_id == tenant_id,
            InboundEvent.status == 'queued').order_by(InboundEvent.received_at, InboundEvent.id)
            .with_for_update()).all()
        batch = [row for row in candidates if row.payload.get('from') == contact]
        if not batch:
            return
        combined = dict(batch[0].payload)
        combined['text'] = '\\n'.join(row.payload.get('text', '') for row in batch if row.payload.get('text'))
        try:
            # This call performs synchronous LLM and HTTP work off the event loop.
            await asyncio.to_thread(whatsapp.handle, tenant_id, combined)
        except Exception as exc:
            for row in batch:
                row.attempts += 1
                row.last_error = str(exc)[:500]
            raise_after_commit = event.attempts < 5
            if not raise_after_commit:
                conv = whatsapp.conversation_for(tenant_id, contact)
                db.execute(text('UPDATE conversations SET handoff = true WHERE id = :id'), {'id': conv.id})
                db.add(Message(conversation_id=conv.id, role='agent',
                               content='Internal note: automated WhatsApp reply failed; agent follow-up required.'))
                for row in batch:
                    row.status = 'failed'
                try:
                    await asyncio.to_thread(whatsapp.send_text, combined['phone_number_id'],
                                            contact, whatsapp.ERROR_REPLY)
                except Exception:
                    pass  # Handoff remains recorded even if Meta is unavailable.
        else:
            for row in batch:
                row.status = 'processed' if row.id == event_id else 'merged'
                row.last_error = None
            raise_after_commit = False
    if raise_after_commit:
        raise Retry(defer=min(2 ** ctx.get('job_try', 1), 60))


class WorkerSettings:
    functions = [sample_job, ingest_source, handle_message]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60
    max_tries = 5


if __name__ == '__main__':
    run_worker(WorkerSettings)
