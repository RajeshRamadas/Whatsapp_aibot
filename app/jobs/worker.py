"""arq worker entry point: python -m app.jobs.worker."""
import asyncio
import logging
from datetime import timedelta

import httpx
from arq import Retry, cron, func, run_worker
from arq.connections import RedisSettings
from sqlalchemy import select, text, update

from .. import ingest, whatsapp
from ..config import settings
from ..db import AuditLog, Conversation, InboundEvent, Message, Session, Source, Tenant, engine, now
from ..integrations.ses import Transport, cache_certificate
from ..observability import bind, configure, observed_job
from ..rate_limits import RateLimiter
from ..services import (
    calendar_availability,
    calendar_notifications,
    calendar_sync,
    evaluation_runs,
    google_jobs,
    lead_sync_worker,
    media,
    payment_notifications,
    payment_processing,
)
from ..services.analytics import reconcile as reconcile_analytics_daily
from ..services.appointment_reminders import process_one as process_appointment_reminder
from ..services.billing import reconcile_settlements, reconcile_subscriptions
from ..services.campaign_delivery import deliver as deliver_campaign
from ..services.campaign_delivery import maintain as maintain_campaigns
from ..services.campaigns import pending as pending_campaigns
from ..services.channels import whatsapp_channel
from ..services.email import deliver_one
from ..services.email import validate_configuration as validate_email
from ..services.events import publish_sync
from ..services.improvements import process_one as process_improvement
from ..services.inbox_push import deliver_waiting
from ..services.templates import eligible_contact, render, send_payload, template_for_send
from ..services.usage import reconcile, send_alerts
from ..services.website_crawl import crawl
from ..services.website_dispatch import pending as pending_websites
from ..services.whatsapp_copy import copy_for
from ..services.widget_turns import process_turn
from .queue import enqueue


@observed_job
async def sample_job(ctx: dict, tenant_id: str, job_id: str) -> str:
    """Persist a tenant-scoped audit event to prove a queued job actually ran."""
    with Session.begin() as db:
        if not db.get(Tenant, tenant_id):
            raise ValueError('Unknown workspace')
        db.add(AuditLog(tenant_id=tenant_id, actor_type='system', action='jobs.sample.completed',
                        target=job_id, details={}))
    return job_id


@observed_job
async def ingest_source(ctx: dict, source_id: str, tenant_id: str) -> None:
    """Process an uploaded source; retry transient failures, fail on final attempt."""
    with Session() as db:
        source = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id))
        if not source or source.tenant_id != tenant_id:
            return  # Deleted source or stale/cross-tenant job.
        if source.status == 'ready':
            return
    try:
        await asyncio.to_thread(ingest.ingest, source_id, tenant_id)
        await asyncio.to_thread(publish_sync, tenant_id, 'source.updated', {'source_id': source_id})
    except Exception as exc:
        with Session.begin() as db:
            source = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id))
            if source and source.tenant_id == tenant_id:
                if isinstance(exc, ValueError) or ctx.get('job_try', 1) >= 3:
                    source.status, source.error = 'failed', str(exc)[:500]
                else:
                    source.status = 'queued'
        await asyncio.to_thread(publish_sync, tenant_id, 'source.updated', {'source_id': source_id})
        if not isinstance(exc, ValueError) and ctx.get('job_try', 1) < 3:
            raise


def process_inbound(event_id):
    """Serialize a contact with a session advisory lock, closing sessions before I/O."""
    with Session() as db:
        event = db.get(InboundEvent, event_id)
        if not event or event.status in ('processed', 'merged', 'failed'):
            return False
        tenant_id, contact = event.tenant_id, event.payload['from']
    bind(tenant_id=tenant_id)
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as lock:
        params = {'key': f'{tenant_id}:whatsapp:{contact}'}
        lock.execute(text('SELECT pg_advisory_lock(hashtextextended(:key, 0))'), params)
        try:
            return process_inbound_locked(event_id, tenant_id, contact)
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key, 0))'), params)


def process_inbound_locked(event_id, tenant_id, contact):
    from ..services.http_action_chat import redact_command
    with Session() as db:
        event = db.get(InboundEvent, event_id)
        if event.status in ('processed', 'merged', 'failed'):
            return False
        candidates = db.scalars(select(InboundEvent).where(
            InboundEvent.tenant_id == tenant_id, InboundEvent.status == 'queued')
            .order_by(InboundEvent.received_at, InboundEvent.id)).all()
        batch = [row for row in candidates if row.payload.get('from') == contact]
        combined = dict(batch[0].payload)
        combined['parts'] = [row.payload for row in batch]
        combined['text'] = '\n'.join(row.payload.get('text', '') for row in batch if row.payload.get('text'))
    try:
        whatsapp.handle(tenant_id, combined)
    except Exception:
        exhausted = False
        with Session.begin() as db:
            rows = db.scalars(select(InboundEvent).where(InboundEvent.tenant_id == tenant_id,
                              InboundEvent.id.in_([row.id for row in batch]))).all()
            for row in rows:
                row.attempts += 1
                row.last_error = 'Automated message processing failed'
            exhausted = max(row.attempts for row in rows) >= 5
            if exhausted:
                for row in rows:
                    row.status = 'failed'
                    row.payload = {**row.payload, 'text': redact_command(row.payload.get('text', ''))}
        if not exhausted:
            return True
        conv = whatsapp.conversation_for(tenant_id, contact)
        with Session.begin() as db:
            db.execute(update(Conversation).where(Conversation.id == conv.id, Conversation.tenant_id == tenant_id)
                       .values(handoff=True, analytics_handoff=True, status='waiting'))
            db.add(Message(conversation_id=conv.id, role='agent', kind='note', direction='outbound',
                           content='Internal note: automated WhatsApp reply failed; agent follow-up required.'))
            tenant = db.get(Tenant, tenant_id)
        try:
            whatsapp.send_text(combined['phone_number_id'], contact, copy_for(tenant)['error'], tenant_id=tenant_id)
        except Exception:
            pass
        publish_sync(tenant_id, 'conversation.updated', {'conversation_id': conv.id})
    else:
        with Session.begin() as db:
            for row in db.scalars(select(InboundEvent).where(InboundEvent.tenant_id == tenant_id,
                                  InboundEvent.id.in_([item.id for item in batch]))):
                row.status = 'processed' if row.id == event_id else 'merged'
                row.payload = {**row.payload, 'text': redact_command(row.payload.get('text', ''))}
                row.last_error = None
    return False


@observed_job
async def handle_message(ctx: dict, event_id: str) -> None:
    await asyncio.sleep(2)
    if await asyncio.to_thread(process_inbound, event_id):
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
        bind(tenant_id=tenant_id, conversation_id=conversation_id)
        channel = whatsapp_channel(db, tenant_id)
        phone = channel.external_id if channel else tenant.whatsapp_phone_number_id
        recipient, content = conversation.external_user, message.content
        eligible = (not tenant.suspended and conversation.handoff and conversation.status == 'agent' and phone
                    and conversation.last_inbound_at
                    and conversation.last_inbound_at > now() - timedelta(hours=24))
        attachment = message.attachments[0] if message.attachments else None
        upload_data = message.upload_data if attachment else None
        template_payload = None
        if message.template_payload is not None:
            queued = message.template_payload
            template = template_for_send(db, channel.id, queued['template_id']) if channel else None
            eligible = bool(not tenant.suspended and conversation.channel == 'whatsapp' and conversation.handoff
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
            if attachment:
                message.attachments = [{**attachment, 'status': 'failed'}]
                message.upload_data = None
    if eligible:
        try:
            if attachment:
                provider_id = media.send(phone, recipient, tenant_id, attachment, upload_data, content)
            elif template_payload is None:
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
                if attachment:
                    message.attachments = [{**attachment, 'status': attachment['status']
                                            if attachment['status'] == 'ready' else 'failed'}]
                    if attachment['status'] == 'ready':
                        message.upload_data = None
                message.channel_message_id = provider_id
                message.delivery_status = 'sent' if provider_id else 'unknown'
                if not provider_id:
                    message.error = 'WhatsApp delivery could not be confirmed'
    publish_sync(tenant_id, 'message.updated', {'conversation_id': conversation_id})
    return True


@observed_job
async def dispatch_agent_replies(ctx: dict) -> None:
    with Session.begin() as db:
        db.execute(update(Message).where(
            Message.role == 'agent', Message.delivery_status == 'sending',
            Message.dispatch_started_at < now() - timedelta(minutes=2),
        ).values(delivery_status='unknown', error='WhatsApp delivery could not be confirmed'))
    for _ in range(20):
        if not await asyncio.to_thread(dispatch_agent_reply):
            break


@observed_job
async def notify_inbox(ctx: dict) -> None:
    await asyncio.to_thread(deliver_waiting)


@observed_job
async def dispatch_widget_turn(ctx: dict) -> None:
    await asyncio.to_thread(process_turn)


@observed_job
async def reconcile_usage(ctx: dict) -> None:
    await asyncio.to_thread(reconcile)


@observed_job
async def dispatch_usage_alerts(ctx: dict) -> None:
    await asyncio.to_thread(send_alerts)


@observed_job
async def reconcile_billing(ctx: dict) -> None:
    await asyncio.to_thread(reconcile_subscriptions)
    await asyncio.to_thread(reconcile_settlements)


@observed_job
async def expire_media(ctx):
    await asyncio.to_thread(media.expire)


@observed_job
async def reconcile_analytics(ctx: dict) -> None:
    await asyncio.to_thread(reconcile_analytics_daily)


@observed_job
async def dispatch_email(ctx):
    if settings.email_backend != 'ses':
        return
    limiter = RateLimiter(settings.email_sends_per_second, window_seconds=1, namespace='email-send')
    for _ in range(20):
        if not await asyncio.to_thread(limiter.allow, 'all'):
            break
        if not await asyncio.to_thread(deliver_one, Transport()):
            break


@observed_job
async def cache_email_certificate(ctx, url):
    try:
        await asyncio.to_thread(cache_certificate, url)
    except httpx.HTTPError as exc:
        permanent = (isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500
                     and exc.response.status_code not in (408, 429))
        if not permanent and ctx.get('job_try', 1) < 5:
            raise Retry(defer=5 * 2 ** ctx.get('job_try', 1)) from None
        raise


@observed_job
async def send_campaign_recipient(ctx, recipient_id, tenant_id):
    await asyncio.to_thread(deliver_campaign, recipient_id, tenant_id)


@observed_job
async def dispatch_campaigns(ctx):
    await asyncio.to_thread(maintain_campaigns)
    for identity, tenant_id, stamp in await asyncio.to_thread(pending_campaigns):
        try:
            await enqueue('send_campaign_recipient', recipient_id=identity, tenant_id=tenant_id,
                          _job_id=f'campaign:{identity}:{stamp.timestamp()}')
        except RuntimeError:
            continue  # Already enqueued; durable state remains the source of truth.


async def startup(ctx):
    configure()
    validate_email()


@observed_job
async def crawl_website(ctx, website_id, tenant_id):
    await crawl(website_id, tenant_id)


@observed_job
async def dispatch_websites(ctx):
    for identity, tenant_id, stamp in await asyncio.to_thread(pending_websites):
        try:
            await enqueue('crawl_website', website_id=identity, tenant_id=tenant_id,
                          _job_id=f'website:{identity}:{stamp.timestamp()}')
        except (OSError, ConnectionError, TimeoutError, RuntimeError):
            continue  # A short database lease allows redispatch after queue failures.


@observed_job
async def publish_improvement(ctx):
    await asyncio.to_thread(process_improvement)


@observed_job
async def dispatch_appointment_reminders(ctx):
    for _ in range(10):
        if not await asyncio.to_thread(process_appointment_reminder):
            break


@observed_job
async def dispatch_google_calendar(ctx):
    await asyncio.to_thread(calendar_availability.schedule)
    for _ in range(10):
        await asyncio.to_thread(calendar_notifications.process_one)
        if (not await asyncio.to_thread(calendar_sync.process_one)
                and not await asyncio.to_thread(google_jobs.process_one)):
            break


@observed_job
async def dispatch_payments(ctx):
    await asyncio.to_thread(payment_notifications.process_one)
    await asyncio.to_thread(payment_processing.expire_payloads)
    for _ in range(10):
        if (not await asyncio.to_thread(payment_processing.process_event)
                and not await asyncio.to_thread(payment_processing.process_one)):
            break


@observed_job
async def dispatch_lead_sync(ctx):
    for _ in range(10):
        if not await asyncio.to_thread(lead_sync_worker.process_one):
            break


@observed_job
async def dispatch_evaluations(ctx):
    for _ in range(10):
        if not await asyncio.to_thread(evaluation_runs.process_one):
            break


@observed_job
async def dispatch_public_operations(ctx):
    from ..services import public_operations
    await asyncio.to_thread(public_operations.expire)
    for _ in range(5):
        if not await asyncio.to_thread(public_operations.process_one):
            break


@observed_job
async def dispatch_outgoing_webhooks(ctx):
    from ..services.http_actions import maintain as maintain_http_actions
    await asyncio.to_thread(maintain_http_actions)
    from ..services import webhook_delivery
    await asyncio.to_thread(webhook_delivery.maintain)
    for _ in range(10):
        if not await webhook_delivery.process_one():
            break
    try:
        await asyncio.to_thread(webhook_delivery.notify_one)
    except Exception:
        logging.getLogger(__name__).warning('Webhook failure notification could not be queued')


class WorkerSettings:
    on_startup = startup
    cron_jobs = [cron(dispatch_outgoing_webhooks, second=set(range(0, 60, 5)), timeout=180),
                 cron(dispatch_public_operations, second=set(range(0, 60, 5)), timeout=900),
                 cron(dispatch_evaluations, second=set(range(0, 60, 5)), timeout=900),
                 cron(dispatch_lead_sync, second=set(range(0, 60, 5)), timeout=900),
                 cron(dispatch_payments, second=set(range(0, 60, 5)), timeout=600),
                 cron(dispatch_google_calendar, second=set(range(0, 60, 5)), timeout=600),
                 cron(dispatch_appointment_reminders, second={0, 15, 30, 45}),
                 cron(publish_improvement, second=set(range(0, 60, 5)), timeout=600),
                 cron(dispatch_websites, second={0, 10, 20, 30, 40, 50}),
                 cron(dispatch_agent_replies, second={0, 10, 20, 30, 40, 50}),
                 cron(dispatch_campaigns, second=set(range(60))),
                 cron(dispatch_email, second=set(range(60))),
                 cron(notify_inbox, second={5, 15, 25, 35, 45, 55}),
                 cron(dispatch_widget_turn, second=set(range(0, 60, 2)), timeout=600),
                 cron(reconcile_usage, hour=1, minute=0, second=0, timeout=600),
                 cron(expire_media, minute=set(range(0, 60, 10)), second=0),
                 cron(reconcile_analytics, hour=1, minute=10, second=0, timeout=600),
                 cron(dispatch_usage_alerts, second=25),
                 cron(reconcile_billing, hour=2, minute=0, second=0, timeout=600)]
    functions = [sample_job, func(ingest_source, timeout=1800), func(handle_message, timeout=600),
                 cache_email_certificate, send_campaign_recipient, func(crawl_website, timeout=3600)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60
    max_tries = 5


if __name__ == '__main__':
    run_worker(WorkerSettings)
