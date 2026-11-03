"""arq worker entry point: python -m app.jobs.worker."""
import asyncio
from datetime import timedelta

from arq import Retry, cron, run_worker
from arq.connections import RedisSettings
from sqlalchemy import select, text, update

from .. import ingest, whatsapp
from ..config import settings
from ..db import AuditLog, Conversation, InboundEvent, Message, Session, Source, Tenant, now
from ..services.channels import whatsapp_channel
from ..services.events import publish_sync
from ..services.inbox_push import deliver_waiting
from ..services.templates import eligible_contact, render, send_payload, template_for_send
from ..services.whatsapp_copy import copy_for
from ..services.widget_turns import process_turn


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
        await asyncio.to_thread(publish_sync, tenant_id, 'source.updated', {'source_id': source_id})
    except Exception as exc:
        with Session.begin() as db:
            source = db.get(Source, source_id)
            if source and source.tenant_id == tenant_id:
                if isinstance(exc, ValueError) or ctx.get('job_try', 1) >= 3:
                    source.status, source.error = 'failed', str(exc)[:500]
                else:
                    source.status = 'queued'
        await asyncio.to_thread(publish_sync, tenant_id, 'source.updated', {'source_id': source_id})
        if not isinstance(exc, ValueError) and ctx.get('job_try', 1) < 3:
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
        combined['parts'] = [row.payload for row in batch]
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
                db.execute(text("UPDATE conversations SET handoff = true, status = 'waiting' WHERE id = :id"),
                           {'id': conv.id})
                db.add(Message(conversation_id=conv.id, role='agent', kind='note', direction='outbound',
                               content='Internal note: automated WhatsApp reply failed; agent follow-up required.'))
                for row in batch:
                    row.status = 'failed'
                try:
                    await asyncio.to_thread(whatsapp.send_text, combined['phone_number_id'],
                                            contact, copy_for(db.get(Tenant, tenant_id))['error'],
                                            tenant_id=tenant_id)
                except Exception:
                    pass  # Handoff remains recorded even if Meta is unavailable.
        else:
            for row in batch:
                row.status = 'processed' if row.id == event_id else 'merged'
                row.last_error = None
            raise_after_commit = False
    if raise_after_commit:
        raise Retry(defer=min(2 ** ctx.get('job_try', 1), 60))


def dispatch_agent_reply() -> bool:
    with Session.begin() as db:
        row = db.execute(
            select(Message, Conversation, Tenant)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(Tenant, Tenant.id == Conversation.tenant_id)
            .where(Message.role == 'agent', Message.delivery_status == 'queued')
            .order_by(Message.id).limit(1).with_for_update(skip_locked=True, of=Message)
        ).first()
        if row is None:
            return False
        message, conversation, tenant = row
        message_id, conversation_id, tenant_id = message.id, conversation.id, tenant.id
        channel = whatsapp_channel(db, tenant_id)
        phone = channel.external_id if channel else tenant.whatsapp_phone_number_id
        recipient, content = conversation.external_user, message.content
        eligible = (conversation.handoff and conversation.status == 'agent' and phone
                    and conversation.last_inbound_at
                    and conversation.last_inbound_at > now() - timedelta(hours=24))
        template_payload = None
        if message.template_payload is not None:
            queued = message.template_payload
            template = template_for_send(db, channel.id, queued['template_id']) if channel else None
            eligible = bool(conversation.channel == 'whatsapp' and conversation.handoff
                            and conversation.status == 'agent' and template and eligible_contact(db, conversation))
            if eligible:
                try:
                    eligible = render(template.body, queued['values']) == content
                except ValueError:
                    eligible = False
            if eligible:
                template_payload = send_payload(template, queued['values'])
        message.delivery_status = 'sending' if eligible else 'failed'
        if eligible:
            message.dispatch_started_at = now()
        if not eligible:
            message.error = 'Conversation is no longer eligible for this WhatsApp reply'
    if eligible:
        try:
            if template_payload is None:
                provider_id = whatsapp.send_text(phone, recipient, content, tenant_id=tenant_id)
            else:
                response = whatsapp.graph_message(phone, {
                    'recipient_type': 'individual', 'to': recipient, 'type': 'template',
                    'template': template_payload,
                }, tenant_id)
                messages = response.json().get('messages') or []
                provider_id = messages[0].get('id') if messages else None
        except Exception:
            provider_id = None
        with Session.begin() as db:
            message = db.get(Message, message_id)
            if message:
                message.channel_message_id = provider_id
                message.delivery_status = 'sent' if provider_id else 'unknown'
                if not provider_id:
                    message.error = 'WhatsApp delivery could not be confirmed'
    publish_sync(tenant_id, 'message.updated', {'conversation_id': conversation_id})
    return True


async def dispatch_agent_replies(ctx: dict) -> None:
    with Session.begin() as db:
        db.execute(update(Message).where(
            Message.role == 'agent', Message.delivery_status == 'sending',
            Message.dispatch_started_at < now() - timedelta(minutes=2),
        ).values(delivery_status='unknown', error='WhatsApp delivery could not be confirmed'))
    for _ in range(20):
        if not await asyncio.to_thread(dispatch_agent_reply):
            break


async def notify_inbox(ctx: dict) -> None:
    await asyncio.to_thread(deliver_waiting)


async def dispatch_widget_turn(ctx: dict) -> None:
    await asyncio.to_thread(process_turn)


class WorkerSettings:
    cron_jobs = [cron(dispatch_agent_replies, second={0, 10, 20, 30, 40, 50}),
                 cron(notify_inbox, second={5, 15, 25, 35, 45, 55}),
                 cron(dispatch_widget_turn, second=set(range(0, 60, 2)), timeout=600)]
    functions = [sample_job, ingest_source, handle_message]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60
    max_tries = 5


if __name__ == '__main__':
    run_worker(WorkerSettings)
