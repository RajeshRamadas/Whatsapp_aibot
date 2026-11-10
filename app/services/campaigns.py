"""Immutable campaign preparation; no provider calls occur in API commands."""
from collections import Counter

from sqlalchemy import case, func, select

from ..config import settings
from ..db import Campaign, CampaignRecipient, Contact, ContactSegment, Session, now
from .audit import audit
from .channels import whatsapp_channel
from .contacts import SegmentFilters, contact_query, marketing_eligible
from .templates import render, template_for_send, variable_count


class CampaignError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def scoped(db, model, identity, tenant_id, lock=False):
    query = select(model).where(model.id == str(identity), model.tenant_id == tenant_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise CampaignError(404, 'Record not found')
    return row


def personalize(bindings, contact):
    values = []
    for binding in bindings:
        field, value = binding['field'], binding.get('value')
        value = (contact.attributes.get(value) if field == 'attribute' else value if field == 'literal'
                 else getattr(contact, field))
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
            raise CampaignError(422, 'A recipient is missing a valid template variable')
        values.append(value.strip())
    return values


def current_template(db, campaign):
    template = template_for_send(db, campaign.channel_id, campaign.template_id)
    if not template or any(getattr(template, key) != campaign.snapshot[key] for key in ('name', 'language', 'body')):
        raise CampaignError(409, 'The approved template changed; create a new campaign')
    return template


def prepare(db, tenant_id, body, actor):
    channel = whatsapp_channel(db, tenant_id)
    template = template_for_send(db, channel.id, str(body.template_id)) if channel else None
    if template is None:
        raise CampaignError(404, 'Approved template not found')
    segment = scoped(db, ContactSegment, body.segment_id, tenant_id)
    filters = SegmentFilters.model_validate({**segment.filters, 'eligible_only': False})
    if filters.channel != 'whatsapp':
        raise CampaignError(422, 'Choose a WhatsApp segment')
    contacts = db.scalars(contact_query(tenant_id, filters).order_by(Contact.id)
                         .limit(settings.campaign_max_recipients + 1)).all()
    if len(contacts) > settings.campaign_max_recipients:
        raise CampaignError(422, 'Audience exceeds the campaign limit')
    bindings = [binding.model_dump() for binding in body.variables]
    if variable_count(template.body) != len(bindings):
        raise CampaignError(422, 'Bind every template variable')
    eligible = [contact for contact in contacts if marketing_eligible(contact)]
    if not eligible:
        raise CampaignError(422, 'No eligible recipients')
    campaign = Campaign(tenant_id=tenant_id, channel_id=channel.id, template_id=template.id, name=body.name,
        snapshot={**{key: getattr(template, key) for key in ('name', 'language', 'body')},
                  'phone_id': channel.external_id}, bindings=bindings,
        excluded=len(contacts) - len(eligible), unit_price=body.unit_price, currency=body.currency)
    db.add(campaign)
    db.flush()
    for contact in eligible:
        values = personalize(bindings, contact)
        render(template.body, values)
        db.add(CampaignRecipient(tenant_id=tenant_id, campaign_id=campaign.id, contact_id=contact.id, values=values))
    audit(db, tenant_id, 'campaign.created', campaign.id, actor_user_id=actor)
    db.flush()
    return campaign


def report(db, campaign):
    accepted = ('sent', 'delivered', 'read')
    match = (CampaignRecipient.tenant_id == campaign.tenant_id, CampaignRecipient.campaign_id == campaign.id)
    groups = db.execute(select(CampaignRecipient.state, CampaignRecipient.is_test, func.count(),
        func.sum(case((CampaignRecipient.replied_at.is_not(None), 1), else_=0)),
        func.sum(case((CampaignRecipient.opted_out_at.is_not(None), 1), else_=0)),
        func.sum(case((CampaignRecipient.billable.is_(True), 1), else_=0)),
        func.sum(case((CampaignRecipient.billable.is_(None), 1), else_=0)))
        .where(*match).group_by(CampaignRecipient.state, CampaignRecipient.is_test)).all()
    counts = Counter({key: 0 for key in ('queued', 'sending', 'sent', 'delivered', 'read', 'failed',
                                        'suppressed', 'unknown', 'cancelled', 'replied', 'opt_outs')})
    total, billable, unconfirmed, tested = 0, 0, 0, False
    for state, is_test, count, replies, optouts, charged, unknown in groups:
        if is_test:
            tested = tested or state in accepted
            continue
        total += count
        counts[state] += count
        counts['replied'] += replies
        counts['opt_outs'] += optouts
        billable += charged
        unconfirmed += unknown if state in accepted else 0
    counts['sent'] += counts['delivered'] + counts['read']
    counts['delivered'] += counts['read']
    preview = db.execute(select(CampaignRecipient.contact_id, CampaignRecipient.values).where(*match,
        CampaignRecipient.is_test.is_(False)).order_by(CampaignRecipient.id).limit(3)).all()
    return {'id': campaign.id, 'name': campaign.name, 'state': campaign.state, 'reason': campaign.reason,
        'scheduled_at': campaign.scheduled_at, 'recipients': total, 'excluded': campaign.excluded,
        'currency': campaign.currency,
        'estimated_max_cost': campaign.unit_price * total if campaign.unit_price is not None else None,
        'estimated_billable_cost': campaign.unit_price * billable if campaign.unit_price is not None else None,
        'pricing_unconfirmed': unconfirmed, 'test_accepted': tested, 'results': dict(counts),
        'preview': [{'contact_id': identity, 'text': render(campaign.snapshot['body'], values)}
                    for identity, values in preview]}


def pending():
    """Durable per-recipient jobs; scheduler can recover after Redis outages."""
    with Session() as db:
        return db.execute(select(CampaignRecipient.id, CampaignRecipient.tenant_id, CampaignRecipient.next_attempt_at)
            .join(Campaign, Campaign.id == CampaignRecipient.campaign_id).where(
                CampaignRecipient.state == 'queued', CampaignRecipient.next_attempt_at <= now(),
                ((CampaignRecipient.is_test.is_(True) & (Campaign.state == 'draft')) |
                 ((Campaign.state.in_(('scheduled', 'sending'))) & (Campaign.scheduled_at <= now()))))
            .order_by(CampaignRecipient.next_attempt_at).limit(100)).all()
