"""Apply provider subscription state and enforce trial and grace entitlements."""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from ..config import settings
from ..db import BillingEvent, Plan, Session, Subscription, Tenant, now
from .analytics import record
from .billing_provider import BillingError, provider

log = logging.getLogger(__name__)
BILLING_NOTICE = 'Automated replies are paused for this workspace. Our team can help.'
TERMINAL = {'cancelled', 'completed', 'expired'}
FAILED = {'pending', 'halted'}


def configured():
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret and settings.razorpay_webhook_secret)


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc) if isinstance(value, int) and value > 0 else None


def entitled(db, tenant, at):
    subscription = db.get(Subscription, tenant.id)
    if not configured() and subscription is None:
        return True
    if subscription:
        if subscription.status == 'active':
            return not subscription.period_end or at < subscription.period_end + timedelta(days=7)
        if subscription.status in FAILED:
            return bool(subscription.grace_until and at < subscription.grace_until)
        if subscription.status in TERMINAL and subscription.period_end:
            return at < subscription.period_end
    return at < tenant.trial_ends_at


def apply_snapshot(tenant, subscription, remote, event=None, at=None, db=None):
    at = at or now()
    if subscription.observed_at and at < subscription.observed_at:
        return
    if remote.get('id') != subscription.provider_id and subscription.provider_id is not None:
        raise BillingError('Provider subscription identity changed')
    state = remote.get('status')
    if state not in {'created', 'authenticated', 'active', 'paused', *FAILED, *TERMINAL}:
        raise BillingError('Unrecognized Razorpay subscription state')
    local_plan = next((key for key, value in settings.razorpay_plan_ids.items()
                       if value == remote.get('plan_id')), None)
    if local_plan is None:
        raise BillingError('Razorpay plan is not mapped to a workspace plan')
    end = timestamp(remote.get('current_end'))
    if event == 'payment.failed' and state == 'active' and (end is None or end <= at):
        state = 'pending'
    previous = (subscription.status, tenant.plan_id)
    subscription.observed_at = at
    subscription.provider_id = remote['id']
    subscription.plan_id = local_plan
    subscription.status = state
    if end:
        subscription.period_end = end
    if state in FAILED:
        if subscription.grace_until is None:
            subscription.grace_until = (end or at) + timedelta(days=7)
    elif state == 'active':
        subscription.grace_until = None
        tenant.plan_id = local_plan
        upgraded = db is not None and (previous[0] != 'active' or (
            db.get(Plan, local_plan).limits.get('price_inr', 0)
            > db.get(Plan, previous[1]).limits.get('price_inr', 0)))
        if upgraded and previous != (state, local_plan):
            record(db, 'upgrade_completed', tenant.id, key=f'{tenant.id}:paid:{remote["id"]}:{local_plan}')
    if isinstance(remote.get('short_url'), str):
        url = urlsplit(remote['short_url'])
        if url.scheme == 'https' and url.hostname in {'rzp.io', 'razorpay.com', 'rzp.in'}:
            subscription.checkout_url = remote['short_url']


def save_snapshot(tenant_id, reference, remote, event=None, at=None, reconciled=False):
    with Session.begin() as db:
        tenant = db.get(Tenant, tenant_id, with_for_update=True)
        row = db.get(Subscription, tenant_id, with_for_update=True)
        if row is None or row.checkout_key != reference:
            raise BillingError('The checkout session changed')
        previous = (row.status, row.plan_id, row.period_end)
        apply_snapshot(tenant, row, remote, event, at, db=db)
        if reconciled and previous != (row.status, row.plan_id, row.period_end):
            row.reconcile_issue = 'Subscription state differed from Razorpay and was repaired'
        elif not reconciled:
            row.reconcile_issue = None
        return row


def reconcile_subscriptions():
    if not configured():
        return
    graph = provider()
    with Session() as db:
        rows = db.scalars(select(Subscription)).all()
    for row in rows:
        try:
            at = now()
            remote = graph.fetch(row.provider_id) if row.provider_id else graph.find_checkout(row.checkout_key)
            if remote is None:
                raise BillingError('Checkout has no confirmed provider subscription')
            save_snapshot(row.tenant_id, row.checkout_key, remote, at=at, reconciled=True)
        except BillingError:
            log.warning('Subscription reconciliation needs attention')
            with Session.begin() as db:
                current = db.get(Subscription, row.tenant_id, with_for_update=True)
                if current and current.checkout_key == row.checkout_key:
                    current.reconcile_issue = 'Could not confirm subscription state with Razorpay'


def reconcile_settlements():
    if not configured():
        return
    at = now()
    with Session() as db:
        pending = db.scalars(select(BillingEvent).where(
            BillingEvent.payment_id.is_not(None), BillingEvent.settled_at.is_(None),
        )).all()
    if not pending:
        return
    graph = provider()
    seen = {}
    try:
        for days in range(8):
            for item in graph.settlements((at - timedelta(days=days)).date()):
                if item.get('type') == 'payment' and item.get('settlement_id'):
                    seen[item.get('entity_id')] = item
    except BillingError:
        log.warning('Could not reconcile Razorpay settlements')
        return
    checked = set()
    issues = {}
    with Session.begin() as db:
        for event in pending:
            if not event.tenant_id:
                continue
            checked.add(event.tenant_id)
            item = seen.get(event.payment_id)
            if item and item.get('amount') == event.amount_paise:
                current = db.get(BillingEvent, event.id)
                if current is not None:
                    current.settled_at = at
            elif item:
                issues[event.tenant_id] = 'A settled payment amount differs from the recorded charge'
            elif event.received_at < at - timedelta(days=7):
                issues.setdefault(event.tenant_id, 'A recorded charge has no matching recent settlement')
        for tenant_id in checked:
            subscription = db.get(Subscription, tenant_id, with_for_update=True)
            if subscription is not None:
                subscription.settlement_issue = issues.get(tenant_id)
