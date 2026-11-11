"""Lead export configuration and transactional outbox; no external calls."""
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import Conversation, Lead, Session, Tenant, now, uid
from ..models.lead_sync import LeadConnection as Connection
from ..models.lead_sync import LeadDelivery as Delivery
from . import crypto
from .appointments import AppointmentError, row
from .audit import audit

SOURCES = ('name', 'phone', 'email', 'need', 'budget', 'location', 'preferred_time', 'status',
           'conversation_url', 'updated_at')


def view(c):
    return {k: getattr(c, k) for k in ('id', 'provider', 'label', 'region', 'status', 'enabled', 'key_last4',
        'config', 'catalog', 'last_success_at', 'last_error')}


def connections(tenant_id):
    with Session() as db:
        return [view(c) for c in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id)
                                           .order_by(Connection.created_at.desc()).limit(30))]


def connect(tenant_id, provider, label, region, credentials, actor=('system', None)):
    packet = crypto.encrypt(tenant_id, json.dumps(credentials))
    with Session.begin() as db:
        db.get(Tenant, tenant_id, with_for_update=True)
        if db.scalar(select(Connection.id).where(Connection.tenant_id == tenant_id, Connection.provider == provider,
                                                  Connection.status != 'disconnected').limit(1)):
            raise AppointmentError('Disconnect this provider’s current account first')
        c = Connection(tenant_id=tenant_id, provider=provider, label=label, region=region, credentials_enc=packet,
                       key_last4=(credentials.get('token') or credentials.get('refresh_token', ''))[-4:])
        db.add(c)
        db.flush()
        queue(db, c, 'discover')
        audit(db, tenant_id, 'lead_sync.connected', c.id, actor_type=actor[0], actor_user_id=actor[1])
        return view(c)


def queue(db, connection, kind, payload=None):
    db.add(Delivery(tenant_id=connection.tenant_id, connection_id=connection.id, kind=kind,
                    revision=connection.revision, payload=payload or {}))


def action(tenant_id, identity, kind, options=None, actor=('system', None)):
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        if kind == 'disconnect':
            c.revision += 1
            c.enabled, c.status, c.credentials_enc, c.oauth_secret_enc = False, 'disconnected', None, None
            c.oauth_state_hash = None
        elif kind in ('discover', 'create_sheet'):
            if not c.credentials_enc or c.status == 'disconnected':
                raise AppointmentError('Connect the account first')
            if kind == 'create_sheet' and c.provider != 'sheets':
                raise AppointmentError('Only Google Sheets can create spreadsheets', 422)
            queue(db, c, kind, options)
        else:
            raise AppointmentError('Unknown connector action', 422)
        audit(db, tenant_id, 'lead_sync.' + kind, identity, actor_type=actor[0], actor_user_id=actor[1])
        return view(c)


def configure(tenant_id, identity, values, actor=('system', None)):
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        if values['enabled']:
            if not values['confirm_sharing'] or c.status != 'connected':
                raise AppointmentError('Test the connection and explicitly approve sharing first', 422)
            if c.provider != 'sheets':
                fields = {f['name']: f for f in c.catalog.get('fields', [])}
                mapping = values['mapping']
                if not mapping or len(set(mapping.values())) != len(mapping):
                    raise AppointmentError('Map each destination field only once', 422)
                if any(k not in SOURCES or v not in fields for k, v in mapping.items()):
                    raise AppointmentError('Choose fields returned by this connection’s test', 422)
                if any(f.get('required') and k not in mapping.values() for k, f in fields.items()):
                    raise AppointmentError('Map every required CRM field', 422)
                targets = {'phone': ('phone', 'mobilephone'), 'email': ('email',)} if c.provider == 'hubspot' else {
                    'phone': ('Phone', 'Mobile'), 'email': ('Email',)}
                if not any(mapping.get(source) in names for source, names in targets.items()):
                    raise AppointmentError('Map phone or email to match CRM records', 422)
            else:
                file = next((f for f in c.catalog.get('files', []) if f['id'] == values['spreadsheet_id']), None)
                if not file or not any(t['id'] == values['tab_id'] for t in file.get('tabs', [])):
                    raise AppointmentError('Load and select an accessible spreadsheet and tab first', 422)
        c.revision += 1
        c.enabled = values['enabled']
        c.config = {k: v for k, v in values.items() if k not in ('enabled', 'confirm_sharing')}
        audit(db, tenant_id, 'lead_sync.configured', identity, actor_type=actor[0], actor_user_id=actor[1],
              details={'enabled': c.enabled})
        return view(c)


def snapshot(lead, connection):
    keys = SOURCES if connection.provider == 'sheets' else connection.config.get('mapping', {}).keys()
    fields = {k: str(lead.fields.get(k, ''))[:500] for k in keys
              if k not in ('status', 'conversation_url', 'updated_at')}
    fields.update(status=lead.status, conversation_url=settings.public_app_url.rstrip('/')
                  + '/dashboard/inbox?conversation=' + lead.conversation_id, updated_at=now().isoformat())
    if connection.config.get('include_notes'):
        fields['notes'] = lead.notes[:8000]
    return fields


def stage_one(db, lead, c):
    statement = insert(Delivery).values(id=uid(), tenant_id=lead.tenant_id, connection_id=c.id,
        conversation_id=lead.conversation_id, kind='sync', lead_version=lead.sync_version,
        revision=c.revision, payload=snapshot(lead, c))
    db.execute(statement.on_conflict_do_nothing(constraint='uq_lead_delivery_version'))


def changed(db, tenant_id, conversation_id):
    lead = db.scalar(select(Lead).where(Lead.tenant_id == tenant_id, Lead.conversation_id == conversation_id)
                     .with_for_update())
    lead.sync_version += 1
    for c in db.scalars(select(Connection).where(Connection.tenant_id == tenant_id, Connection.enabled,
                                                 Connection.status != 'disconnected')):
        stage_one(db, lead, c)


def backfill(tenant_id, identity, lead_ids, actor=('system', None)):
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity, lock=True)
        if not c.enabled or c.status == 'disconnected':
            raise AppointmentError('Enable sharing for this connection first')
        for conversation_id in lead_ids:
            row(db, Conversation, tenant_id, conversation_id)
            lead = db.scalar(select(Lead).where(Lead.tenant_id == tenant_id, Lead.conversation_id == conversation_id))
            if not lead:
                raise AppointmentError('Lead not found', 404)
            stage_one(db, lead, c)
        audit(db, tenant_id, 'lead_sync.backfill', identity, actor_type=actor[0], actor_user_id=actor[1],
              details={'count': len(lead_ids)})
    return {'queued': len(lead_ids)}


def jobs(tenant_id, identity):
    with Session() as db:
        row(db, Connection, tenant_id, identity)
        return [{k: getattr(j, k) for k in ('id', 'conversation_id', 'kind', 'lead_version', 'state', 'attempts',
                                            'last_error', 'created_at')} for j in db.scalars(select(Delivery).where(
            Delivery.tenant_id == tenant_id, Delivery.connection_id == identity)
            .order_by(Delivery.created_at.desc(), Delivery.id).limit(100))]


def retry(tenant_id, identity, job_id, actor=('system', None)):
    with Session.begin() as db:
        c = row(db, Connection, tenant_id, identity)
        j = row(db, Delivery, tenant_id, job_id, lock=True)
        if j.connection_id != identity:
            raise AppointmentError('Delivery not found', 404)
        if j.state != 'failed' or j.revision != c.revision or c.status == 'disconnected':
            raise AppointmentError('Only failed deliveries of the current configuration can be retried')
        j.state, j.attempts, j.next_attempt_at, j.last_error = 'queued', 0, now(), None
        audit(db, tenant_id, 'lead_sync.retried', job_id, actor_type=actor[0], actor_user_id=actor[1])
    return {'queued': 1}
