"""Workspace checkout and signed Razorpay webhook processing."""
import hashlib
import hmac
import json
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import BillingEvent, Plan, Session, Subscription, Tenant, now, uid
from ..services.analytics import record
from ..services.billing import TERMINAL, apply_snapshot, configured, entitled, save_snapshot
from ..services.billing_provider import BillingError, provider
from ..services.invoices import issue
from .principal import tenant_for_role

router = APIRouter()
EVENTS = {'subscription.activated', 'subscription.charged', 'subscription.pending',
          'subscription.halted', 'subscription.cancelled', 'payment.failed'}


class PlanChoice(BaseModel):
    plan: Literal['starter', 'growth', 'business']


def public_view(row):
    return {'plan': row.plan_id, 'status': row.status, 'period_end': row.period_end,
            'grace_until': row.grace_until, 'checkout_url': row.checkout_url,
            'reconcile_issue': row.reconcile_issue, 'settlement_issue': row.settlement_issue}


def billing_provider():
    if not configured():
        raise HTTPException(503, 'Razorpay subscriptions are not configured')
    return provider()


def mapped(plan):
    value = settings.razorpay_plan_ids.get(plan)
    if not value:
        raise HTTPException(503, 'This plan is not available for checkout')
    return value


@router.get('/api/v1/billing')
def status(tenant: Tenant = Depends(tenant_for_role('owner'))):
    with Session() as db:
        row = db.get(Subscription, tenant.id)
        plans = db.scalars(select(Plan).order_by(Plan.id)).all()
        return {'enabled': configured(), 'trial_ends_at': tenant.trial_ends_at, 'current_plan': tenant.plan_id,
                'timezone': tenant.timezone,
                'tax_rate': str(settings.gst_invoice_issuer.tax_rate) if settings.gst_invoice_issuer else None,
                'entitled': entitled(db, tenant, now()), 'subscription': public_view(row) if row else None,
                'plans': [{'id': plan.id, 'price_inr': plan.limits.get('price_inr'), 'limits': plan.limits,
                           'available': bool(settings.razorpay_plan_ids.get(plan.id))} for plan in plans]}


@router.post('/api/v1/billing/checkout')
def checkout(body: PlanChoice, tenant: Tenant = Depends(tenant_for_role('owner'))):
    graph = billing_provider()
    remote_plan = mapped(body.plan)
    with Session.begin() as db:
        workspace = db.get(Tenant, tenant.id, with_for_update=True)
        if settings.gst_invoice_issuer is not None and not workspace.billing_details:
            raise HTTPException(409, 'Save your billing details before checkout')
        trial_end = workspace.trial_ends_at
        row = db.get(Subscription, tenant.id, with_for_update=True)
        new = row is None or (row.status in TERMINAL and (not row.period_end or row.period_end <= now()))
        if new:
            if db.get(Plan, body.plan) is None:
                raise HTTPException(404, 'Plan not found')
            reference = uid()
            if row is None:
                row = Subscription(tenant_id=tenant.id, plan_id=body.plan, checkout_key=reference, status='creating')
                db.add(row)
            else:
                row.checkout_key, row.plan_id, row.status = reference, body.plan, 'creating'
                row.provider_id = row.checkout_url = row.period_end = row.grace_until = row.reconcile_issue = None
                row.observed_at = None
            record(db, 'upgrade_started', tenant.id, key=f'{tenant.id}:checkout:{reference}')
        else:
            if row.status not in ('creating', 'created', 'authenticated') or row.plan_id != body.plan:
                raise HTTPException(409, 'Manage the existing subscription before starting another checkout')
            if row.checkout_url:
                return public_view(row)
            reference = row.checkout_key

    try:
        at = now()
        if new:
            start = int(trial_end.timestamp()) if (trial_end - now()).total_seconds() > 60 else None
            remote = graph.checkout(remote_plan, start, reference)
        else:
            remote = graph.find_checkout(reference)
        if remote is None:
            raise BillingError('Checkout is still being confirmed. Try again shortly.')
        return public_view(save_snapshot(tenant.id, reference, remote, at=at))
    except BillingError as exc:
        raise HTTPException(502, str(exc)) from None


@router.patch('/api/v1/billing/subscription')
def change_plan(body: PlanChoice, tenant: Tenant = Depends(tenant_for_role('owner'))):
    graph = billing_provider()
    remote_plan = mapped(body.plan)
    with Session() as db:
        row = db.get(Subscription, tenant.id)
        if row is None or row.status != 'active' or not row.provider_id:
            raise HTTPException(409, 'An active subscription is required')
        current = db.get(Plan, row.plan_id)
        target = db.get(Plan, body.plan)
        if target is None:
            raise HTTPException(404, 'Plan not found')
        if target.id == current.id:
            return public_view(row)
        immediate = target.limits['price_inr'] > current.limits['price_inr']
    if immediate:
        with Session.begin() as db:
            record(db, 'upgrade_started', tenant.id, key=f'{tenant.id}:upgrade:{row.checkout_key}:{body.plan}')
    try:
        at = now()
        remote = graph.change(row.provider_id, remote_plan, immediate)
        return public_view(save_snapshot(tenant.id, row.checkout_key, remote, at=at))
    except BillingError as exc:
        raise HTTPException(502, str(exc)) from None


@router.post('/api/v1/billing/subscription/cancel')
def cancel(tenant: Tenant = Depends(tenant_for_role('owner'))):
    graph = billing_provider()
    with Session() as db:
        row = db.get(Subscription, tenant.id)
        if row is None or not row.provider_id:
            raise HTTPException(409, 'No confirmed subscription exists')
        if row.status in TERMINAL:
            return public_view(row)
    try:
        at = now()
        remote = graph.cancel(row.provider_id, row.status == 'active')
        return public_view(save_snapshot(tenant.id, row.checkout_key, remote, at=at))
    except BillingError as exc:
        raise HTTPException(502, str(exc)) from None


def entity(payload, name):
    return ((payload.get(name) or {}).get('entity') or {})


def process_event(event_id, name, payload):
    with Session() as db:
        if db.get(BillingEvent, event_id):
            return
    graph = provider()
    subscription = entity(payload, 'subscription')
    provider_id = subscription.get('id')
    if not provider_id and name == 'payment.failed':
        provider_id = graph.subscription_for_payment(entity(payload, 'payment'))
    at = now()
    remote = graph.fetch(provider_id) if provider_id else None
    reference = ((remote or {}).get('notes') or {}).get('checkout_key')
    with Session.begin() as db:
        statement = insert(BillingEvent).values(id=event_id, event=name).on_conflict_do_nothing(
            index_elements=['id']).returning(BillingEvent.id)
        if db.execute(statement).scalar_one_or_none() is None or remote is None:
            return
        target = db.scalar(select(Subscription).where(or_(
            Subscription.provider_id == provider_id,
            (Subscription.checkout_key == reference) & Subscription.provider_id.is_(None),
        )))
        if target is None:
            return
        tenant = db.get(Tenant, target.tenant_id, with_for_update=True)
        row = db.get(Subscription, target.tenant_id, with_for_update=True, populate_existing=True)
        if row and (row.provider_id == provider_id or (row.provider_id is None and row.checkout_key == reference)):
            apply_snapshot(tenant, row, remote, name, at, db=db)
            payment = entity(payload, 'payment')
            if name == 'subscription.charged' and payment.get('id') and isinstance(payment.get('amount'), int):
                stored = db.get(BillingEvent, event_id)
                stored.tenant_id, stored.payment_id, stored.amount_paise = tenant.id, payment['id'], payment['amount']
                issue(db, tenant, payment)
            row.reconcile_issue = None


@router.post('/webhooks/razorpay')
async def webhook(request: Request, signature: str = Header('', alias='X-Razorpay-Signature'),
                  event_id: str = Header('', alias='X-Razorpay-Event-Id', max_length=128)):
    if not configured():
        raise HTTPException(503, 'Razorpay subscriptions are not configured')
    raw = await request.body()
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(401, 'Invalid webhook signature')
    if not event_id:
        raise HTTPException(400, 'Missing webhook event ID')
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, 'Invalid webhook body') from None
    if not isinstance(data, dict):
        raise HTTPException(400, 'Invalid webhook body')
    name = data.get('event')
    if isinstance(name, str) and name in EVENTS:
        try:
            await run_in_threadpool(process_event, event_id, name, data.get('payload') or {})
        except BillingError as exc:
            raise HTTPException(502, str(exc)) from None
    return {'status': 'ok'}
