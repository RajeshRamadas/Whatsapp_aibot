"""Worker-only Meta delivery, conservative capacity control and authenticated feedback."""
import secrets
from datetime import timedelta

import httpx
from redis.exceptions import RedisError
from sqlalchemy import exists, func, select, text, update

from .. import whatsapp
from ..config import settings
from ..db import Campaign, Channel, Contact, Session, Tenant, now
from ..db import CampaignRecipient as Recipient
from ..rate_limits import RateLimiter
from .analytics import record
from .audit import audit
from .campaigns import CampaignError, current_template, scoped
from .channels import access_token
from .contacts import marketing_eligible
from .events import publish_sync
from .meta_signup import MetaSignup, MetaSignupError
from .templates import send_payload

TIERS = {'TIER_50': 50, 'TIER_250': 250, 'TIER_1K': 1000, 'TIER_2K': 2000, 'TIER_10K': 10000,
         'TIER_100K': 100000, 'TIER_UNLIMITED': 1000000000}
RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def notify(tenant_id, campaign_id):
    publish_sync(tenant_id, 'campaign.updated', {'campaign_id': campaign_id})


def quality(channel_id, tenant_id, rating, limit=None, *, verified=False, portfolio=None):
    changed = []
    with Session.begin() as db:
        channel = scoped(db, Channel, channel_id, tenant_id, lock=True)
        channel.campaign_quality = rating if rating in ('GREEN', 'YELLOW', 'RED') else 'UNKNOWN'
        channel.campaign_checked_at = now() if verified else None
        if portfolio:
            channel.campaign_portfolio = portfolio
        if limit is not None:
            channel.campaign_limit = limit
        if channel.campaign_quality != 'GREEN' or channel.campaign_limit == 0:
            for campaign in db.scalars(select(Campaign).where(Campaign.tenant_id == tenant_id,
                    Campaign.channel_id == channel_id, Campaign.state.in_(('scheduled', 'sending'))).with_for_update()):
                campaign.state, campaign.reason = 'paused', 'channel_quality'
                changed.append(campaign.id)
                audit(db, tenant_id, 'campaign.paused', campaign.id, {'reason': 'channel_quality'}, actor_type='system')
    for identity in changed:
        notify(tenant_id, identity)


def refresh(channel_id, tenant_id):
    with Session() as db:
        channel = scoped(db, Channel, channel_id, tenant_id)
        phone, checked, waba = channel.external_id, channel.campaign_checked_at, channel.waba_id
        if channel.campaign_portfolio and checked and checked > now() - timedelta(minutes=5):
            return True
        if not waba:
            return False
    try:
        token = access_token(phone, tenant_id)
        result = MetaSignup().request(phone, token=token,
            params={'fields': 'quality_rating,whatsapp_business_manager_messaging_limit'})
        account = MetaSignup().request(waba, token=token, params={'fields': 'owner_business_info'})
    except (MetaSignupError, ValueError, RuntimeError):
        return False
    owner = account.get('owner_business_info')
    portfolio = owner.get('id') if isinstance(owner, dict) else None
    if (not isinstance(portfolio, str) or not 1 <= len(portfolio) <= 64
            or not portfolio.isascii() or not portfolio.isdigit()):
        return False
    tier = result.get('whatsapp_business_manager_messaging_limit')
    quality(channel_id, tenant_id, result.get('quality_rating'),
            TIERS.get(tier, 0) if isinstance(tier, str) else 0, verified=True, portfolio=portfolio)
    return True


def quality_event(waba_id, value):
    event = value.get('event')
    capacity = value.get('max_daily_conversations_per_business')
    if event not in ('FLAGGED', 'UNFLAGGED', 'UPGRADE', 'DOWNGRADE') and capacity is None:
        return
    with Session() as db:
        channels = db.scalars(select(Channel).where(Channel.waba_id == waba_id, Channel.kind == 'whatsapp')).all()
    # A WABA event never raises a phone's allowance. A worker refresh verifies it first.
    for channel in channels:
        tier = capacity if capacity is not None else value.get('current_limit')
        limit = TIERS.get(tier, 0) if isinstance(tier, str) else (
            tier if type(tier) is int and tier in TIERS.values() else 0)
        change = event if capacity is None else ('DOWNGRADE' if limit < channel.campaign_limit else 'UPGRADE')
        if change in ('FLAGGED', 'DOWNGRADE'):
            quality(channel.id, channel.tenant_id, 'RED' if change == 'FLAGGED' else 'UNKNOWN',
                    limit if change == 'DOWNGRADE' else None)
        else:
            with Session.begin() as db:
                scoped(db, Channel, channel.id, channel.tenant_id, lock=True).campaign_checked_at = None


def maintain():
    changed = set()
    with Session.begin() as db:
        changed.update(db.execute(update(Recipient).where(Recipient.state == 'sending',
            Recipient.claimed_at < now() - timedelta(minutes=2)).values(state='unknown', error_code='delivery_unknown')
            .returning(Recipient.tenant_id, Recipient.campaign_id)).all())
        changed.update(db.execute(update(Campaign).where(Campaign.state.in_(('scheduled', 'sending')),
            Campaign.channel_id.is_(None)).values(state='paused', reason='channel_unlinked')
            .returning(Campaign.tenant_id, Campaign.id)).all())
        unfinished = exists(select(Recipient.id).where(Recipient.campaign_id == Campaign.id,
            Recipient.tenant_id == Campaign.tenant_id, Recipient.is_test.is_(False),
            Recipient.state.in_(('queued', 'sending'))))
        completed = db.execute(update(Campaign).where(Campaign.state.in_(('scheduled', 'sending')), ~unfinished)
            .values(state='completed').returning(Campaign.tenant_id, Campaign.id)).all()
        changed.update(completed)
        from .outgoing_webhooks import stage
        for tenant_id, identity in completed:
            stage(db, tenant_id, 'campaign.completed', 'campaign.completed:' + identity,
                  {'campaign_id': identity, 'state': 'completed'})
    for tenant_id, identity in changed:
        notify(tenant_id, identity)


def deliver(identity, tenant_id):
    with Session() as db:
        recipient = scoped(db, Recipient, identity, tenant_id)
        campaign = scoped(db, Campaign, recipient.campaign_id, tenant_id)
        channel_id = campaign.channel_id
    if channel_id is None:
        return
    ready = refresh(channel_id, tenant_id)
    with Session.begin() as db:
        channel = scoped(db, Channel, channel_id, tenant_id, lock=True)
        campaign = scoped(db, Campaign, recipient.campaign_id, tenant_id, lock=True)
        row = scoped(db, Recipient, identity, tenant_id, lock=True)
        if row.state != 'queued' or row.next_attempt_at > now():
            return
        if not ((row.is_test and campaign.state == 'draft') or
                (not row.is_test and campaign.state in ('scheduled', 'sending') and campaign.scheduled_at <= now())):
            return
        row.next_attempt_at = now() + timedelta(seconds=60)
        if campaign.snapshot.get('phone_id') not in (None, channel.external_id):
            campaign.state, campaign.reason = 'paused', 'channel_changed'
            return
        if not campaign.snapshot.get('phone_id'):
            campaign.snapshot = {**campaign.snapshot, 'phone_id': channel.external_id}
        contact = scoped(db, Contact, row.contact_id, tenant_id)
        tenant = db.get(Tenant, tenant_id)
        if not marketing_eligible(contact):
            row.state, row.error_code = 'suppressed', 'opted_out'
            return
        if (tenant.suspended or not ready or not channel.campaign_portfolio
                or channel.campaign_quality != 'GREEN' or channel.campaign_limit < 1):
            row.error_code = 'channel_unavailable'
            return
        try:
            template = current_template(db, campaign)
        except CampaignError:
            campaign.state, campaign.reason = 'paused', 'template_changed'
            return
        campaign.snapshot = {**campaign.snapshot, 'portfolio_id': channel.campaign_portfolio}
        # Meta's portfolio budget crosses phone/workspace boundaries. Only aggregate capacity is read.
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                   {'key': 'campaign-portfolio:' + channel.campaign_portfolio})
        cap = db.scalar(select(func.min(Channel.campaign_limit)).where(
            Channel.campaign_portfolio == channel.campaign_portfolio))
        reservations = select(Contact.external_user).join(Recipient, Recipient.contact_id == Contact.id)\
            .join(Campaign, Campaign.id == Recipient.campaign_id).where(
            Campaign.snapshot['portfolio_id'].astext == channel.campaign_portfolio,
            Recipient.claimed_at >= now() - timedelta(days=1))
        used = db.scalar(select(func.count()).select_from(reservations.distinct().subquery()))
        already = db.scalar(reservations.where(Contact.external_user == contact.external_user).limit(1))
        if not already and used >= min(cap, settings.campaign_daily_limit):
            row.error_code = 'daily_limit'
            return
        try:
            allowed = RateLimiter(settings.campaign_sends_per_second, window_seconds=1,
                                  namespace='campaign-send').allow(channel.external_id)
        except RedisError:
            allowed = False
        if not allowed:
            row.next_attempt_at, row.error_code = now() + timedelta(seconds=1), 'rate_limit'
            return
        row.state, row.attempts, row.claimed_at = 'sending', row.attempts + 1, now()
        row.error_code = None
        if not row.is_test:
            campaign.state = 'sending'
        phone, recipient_phone, payload = channel.external_id, contact.external_user, send_payload(template, row.values)
        attempt, is_test, campaign_id = row.attempts, row.is_test, campaign.id
    error, delay, state, provider_id = None, 0, 'sent', None
    try:
        response = whatsapp.graph_message(phone, {'recipient_type': 'individual', 'to': recipient_phone,
            'type': 'template', 'template': payload, 'biz_opaque_callback_data': identity}, tenant_id)
        provider_id = response.json()['messages'][0]['id']
        if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 128:
            raise ValueError()
    except httpx.HTTPStatusError as exc:
        try:
            code = int(exc.response.json().get('error', {}).get('code', 0))
        except (ValueError, TypeError, AttributeError):
            code = 0
        error = f'meta_{code}' if 0 < code < 1000000000 else f'http_{exc.response.status_code}'
        transient = exc.response.status_code == 429 or exc.response.status_code >= 500 or code in (
            4, 17, 32, 613, 80007, 130429, 131000, 131016, 131056)
        state = 'queued' if transient and attempt < 5 else 'failed'
        retry = exc.response.headers.get('Retry-After', '')
        delay = min(int(retry), 3600) if retry.isascii() and retry.isdigit() and len(retry) < 5 else 0
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, RuntimeError):
        state, error = 'unknown', 'delivery_unknown'  # Ambiguous sends are never retried automatically.
    with Session.begin() as db:
        row = scoped(db, Recipient, identity, tenant_id, lock=True)
        if row.state == 'sending' and row.attempts == attempt:
            row.state, row.error_code = state, error
            if state == 'sent':
                row.provider_id, row.accepted_at = provider_id, row.claimed_at
            row.next_attempt_at = now() + timedelta(seconds=max(delay, 5 * 2 ** attempt + secrets.randbelow(5)))
        if row.state in RANK and not is_test:
            record(db, 'campaign_sent', tenant_id, key=f'{campaign_id}:campaign_sent')
    if error == 'meta_131048':
        quality(channel_id, tenant_id, 'RED')
    notify(tenant_id, campaign_id)


def receipt(status):
    if not isinstance(status.get('id'), str) or len(status['id']) > 128:
        return
    with Session.begin() as db:
        channel = db.scalar(select(Channel).where(Channel.kind == 'whatsapp',
            Channel.external_id == status['phone_number_id']))
        if channel is None:
            return
        query = select(Recipient).join(Campaign, Campaign.id == Recipient.campaign_id).where(
            Recipient.tenant_id == channel.tenant_id, Campaign.tenant_id == channel.tenant_id,
            Campaign.channel_id == channel.id, Recipient.claimed_at.is_not(None),
            Recipient.state.in_(('sending', 'sent', 'delivered', 'read', 'failed', 'unknown')))
        opaque = status.get('biz_opaque_callback_data')
        opaque = opaque if isinstance(opaque, str) and len(opaque) <= 36 else None
        row = db.scalar(query.where((Recipient.provider_id == status['id']) |
            ((Recipient.id == opaque) & Recipient.provider_id.is_(None))).with_for_update(of=Recipient))
        if row is None:
            return
        row.provider_id = status['id']
        incoming = status['status']
        if RANK.get(incoming, 0) > RANK.get(row.state, 0) and not (row.state == 'failed' and incoming == 'sent'):
            row.state, row.error_code = incoming, None
            row.accepted_at = row.accepted_at or row.claimed_at
        elif incoming == 'failed' and row.state not in ('delivered', 'read'):
            row.state, row.error_code = 'failed', 'provider_rejected'
        billable = (status.get('pricing') or {}).get('billable')
        if isinstance(billable, bool) and (row.billable is None or billable):
            row.billable = billable
        if row.state in RANK and not row.is_test:
            record(db, 'campaign_sent', row.tenant_id, key=f'{row.campaign_id}:campaign_sent')
        campaign_id = row.campaign_id
    notify(channel.tenant_id, campaign_id)


def response(tenant_id, contact_id, context_id, opted_out):
    with Session.begin() as db:
        query = select(Recipient).where(Recipient.tenant_id == tenant_id, Recipient.contact_id == contact_id,
            Recipient.is_test.is_(False), Recipient.accepted_at.is_not(None), Recipient.accepted_at <= now())
        query = query.where(Recipient.provider_id == context_id) if context_id else query.where(
            Recipient.accepted_at >= now() - timedelta(days=7))
        row = db.scalar(query.order_by(Recipient.accepted_at.desc(), Recipient.id).limit(1).with_for_update())
        if row:
            row.replied_at = row.replied_at or now()
            if opted_out:
                row.opted_out_at = row.opted_out_at or now()
            campaign_id = row.campaign_id
    if row:
        notify(tenant_id, campaign_id)
