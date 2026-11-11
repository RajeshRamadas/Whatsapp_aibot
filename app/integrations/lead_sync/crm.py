"""HubSpot v3 Contacts and Zoho v8 Leads; bounded identity matching and durable notes."""
import html
import json
import re
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from ...config import settings
from ...db import Session, now
from ...models.lead_sync import LeadConnection as Connection
from ...models.lead_sync import LeadDelivery as Delivery
from ...services import crypto
from .http import HTTP, SyncError

REGIONS = {'in': 'in', 'us': 'com', 'eu': 'eu', 'au': 'com.au'}
FIELD = re.compile(r'[a-zA-Z][a-zA-Z0-9_]{0,99}')


def identity(value):
    if not re.fullmatch(r'[0-9]{1,100}', str(value)):
        raise SyncError('invalid_response')
    return str(value)


def normalized(value, kind):
    if kind == 'phone':
        digits = re.sub(r'\D', '', str(value or ''))
        return digits if 7 <= len(digits) <= 15 else ''
    try:
        return validate_email(str(value or ''), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return ''


class CRM:
    def __init__(self, tenant_id, connection, credentials):
        self.tenant_id, self.connection = tenant_id, connection
        self.hub = connection.provider == 'hubspot'
        self.prefix = 'crm/' + (settings.lead_sync_hubspot_api_version if self.hub
                                else settings.lead_sync_zoho_api_version) + '/'
        if self.hub:
            self.http = HTTP(tenant_id, 'hubspot', 'https://api.hubapi.com/',
                             {'Authorization': 'Bearer ' + credentials['token']})
        else:
            suffix = REGIONS[connection.region]
            expiry = datetime.fromisoformat(credentials.get('expires_at') or '1970-01-01T00:00:00+00:00')
            if expiry <= now() + timedelta(minutes=5):
                data = HTTP(tenant_id, 'zoho', f'https://accounts.zoho.{suffix}/oauth/v2/').request('POST', 'token',
                    data={k: credentials[k] for k in ('client_id', 'client_secret', 'refresh_token')}
                         | {'grant_type': 'refresh_token'})
                if not data.get('access_token') or not 300 <= int(data.get('expires_in', 0)) <= 86400:
                    raise SyncError('credentials_required')
                credentials.update(access_token=data['access_token'],
                    expires_at=(now() + timedelta(seconds=int(data['expires_in']))).isoformat())
                packet = crypto.encrypt(tenant_id, json.dumps(credentials))
                with Session.begin() as db:
                    current = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id,
                        Connection.id == connection.id).with_for_update())
                    if not current or current.revision != connection.revision or current.status == 'disconnected':
                        raise SyncError('connection_changed')
                    current.credentials_enc = packet
            self.http = HTTP(tenant_id, 'zoho', f'https://www.zohoapis.{suffix}/' + self.prefix,
                             {'Authorization': 'Zoho-oauthtoken ' + credentials['access_token']})
        self.root = self.prefix + 'objects/contacts' if self.hub else 'Leads'

    def inventory(self, file_id=None, previous=None):
        data = self.http.request('GET', self.prefix + 'properties/contacts' if self.hub else 'settings/fields',
                                 params=None if self.hub else {'module': 'Leads'})
        fields = []
        for f in data.get('results' if self.hub else 'fields', []):
            name = f.get('name' if self.hub else 'api_name', '')
            if not FIELD.fullmatch(name):
                continue
            if self.hub:
                allowed = (f.get('type') in ('string', 'enumeration') and not f.get('archived')
                           and not f.get('modificationMetadata', {}).get('readOnlyValue'))
            else:
                allowed = (f.get('data_type') in ('text', 'email', 'phone', 'textarea', 'picklist', 'website')
                           and not f.get('field_read_only') and not f.get('read_only')
                           and f.get('operation_type', {}).get('api_create', True)
                           and f.get('operation_type', {}).get('api_update', True))
            if allowed:
                fields.append({'name': name, 'label': str(f.get('label') or f.get('field_label') or name)[:100],
                               'required': bool(f.get('system_mandatory'))})
        if not fields:
            raise SyncError('no_writable_fields')
        return {'fields': fields[:500]}

    def match(self, value, kind):
        key = normalized(value, kind)
        if not key:
            return None
        names = (['phone', 'mobilephone'] if self.hub else ['Phone', 'Mobile']) if kind == 'phone' else (
                 ['email'] if self.hub else ['Email'])
        variants = list(dict.fromkeys([str(value), key, '+' + key] if kind == 'phone' else [key]))
        if self.hub:
            groups = [{'filters': [{'propertyName': name, 'operator': 'EQ', 'value': v}]}
                      for name in names for v in variants]
            # HubSpot accepts at most five filter groups per search request.
            rows = []
            for offset in range(0, len(groups), 5):
                data = self.http.request('POST', self.root + '/search', payload={
                    'filterGroups': groups[offset:offset+5],
                    'properties': names, 'limit': 100})
                if data.get('paging') or data.get('total', 0) > 100:
                    raise SyncError('ambiguous_identity')
                rows.extend(data.get('results', []))
            rows = [{'id': r.get('id'), **r.get('properties', {})} for r in rows]
        else:
            def escape(v):
                return re.sub(r'([\\(),:])', r'\\\1', v)
            criteria = '(' + 'or'.join(f'({name}:equals:{escape(v)})' for name in names for v in variants) + ')'
            data = self.http.request('GET', self.root + '/search', params={'criteria': criteria,
                                     'fields': ','.join(names), 'per_page': 200})
            if data.get('info', {}).get('more_records'):
                raise SyncError('ambiguous_identity')
            rows = data.get('data', [])
        matches = {identity(r.get('id')) for r in rows if any(normalized(r.get(n), kind) == key for n in names)}
        if len(matches) > 1:
            raise SyncError('ambiguous_identity')
        return next(iter(matches), None)

    def previous(self, job):
        with Session() as db:
            rows = db.execute(select(Delivery.remote_id, Delivery.submitted).where(
                Delivery.tenant_id == self.tenant_id, Delivery.connection_id == job.connection_id,
                Delivery.conversation_id == job.conversation_id, Delivery.kind == 'sync', Delivery.id != job.id,
                Delivery.lead_version <= job.lead_version).order_by(Delivery.lead_version.desc())).all()
            return next((identity(r.remote_id) for r in rows if r.remote_id), None), any(
                r.submitted and not r.remote_id for r in rows)

    def result_id(self, result):
        if self.hub:
            return identity(result.get('id'))
        rows = result.get('data', [])
        item = rows[0] if len(rows) == 1 else {}
        if item.get('code') != 'SUCCESS':
            if item.get('code') == 'DUPLICATE_DATA':
                raise SyncError('record_conflict', retry=True, wait=5)
            raise SyncError('record_rejected')
        return identity(item.get('details', {}).get('id'))

    def note_exists(self, contact, marker):
        after = None
        for page in range(1, 4):
            if self.hub:
                data = self.http.request('GET', self.root + '/' + contact + '/associations/notes',
                                         params={'limit': 100, **({'after': after} if after else {})})
                ids = [{'id': identity(r.get('id'))} for r in data.get('results', [])]
                notes = self.http.request('POST', self.prefix + 'objects/notes/batch/read', payload={
                    'properties': ['hs_note_body'], 'inputs': ids}).get('results', []) if ids else []
                texts = [n.get('properties', {}).get('hs_note_body', '') for n in notes]
                after = data.get('paging', {}).get('next', {}).get('after')
            else:
                data = self.http.request('GET', self.root + '/' + contact + '/Notes', params={
                    'fields': 'Note_Content', 'per_page': 200, 'page': page})
                texts = [n.get('Note_Content', '') for n in data.get('data', [])]
                after = data.get('info', {}).get('more_records')
            if any(marker in str(text) for text in texts):
                return True
            if not after:
                break
        return False

    def deliver(self, job, config, checkpoint):
        payload, mapping = job.payload, config.get('mapping', {})
        properties = {target: str(payload.get(source, '')) for source, target in mapping.items()}
        if any(f.get('required') and not properties.get(f['name'], '').strip()
               for f in self.connection.catalog.get('fields', [])):
            raise SyncError('required_field_missing')
        if not any(normalized(payload.get(k), k) for k in ('phone', 'email') if k in mapping):
            raise SyncError('missing_identity')
        previous, unknown = self.previous(job)
        contact = job.remote_id or previous
        if not contact:
            contact = self.match(payload.get('phone'), 'phone') or self.match(payload.get('email'), 'email')
        if not job.contact_done:
            if not contact and (job.submitted or unknown):
                raise SyncError('creation_unconfirmed', retry=True)
            checkpoint(**({} if contact else {'submitted': True}))
            body = {'properties': properties} if self.hub else {'data': [properties]}
            result = self.http.request(('PATCH' if self.hub else 'PUT') if contact else 'POST',
                                       self.root + ('/' + identity(contact) if contact else ''), payload=body)
            contact = self.result_id(result)
            checkpoint(remote_id=contact, contact_done=True)
        contact = identity(contact)
        marker = '[marketingadda:' + job.id + ']'
        if job.note_submitted:
            if self.note_exists(contact, marker):
                return
            raise SyncError('note_unconfirmed', retry=True)
        summary = '\n'.join([marker, 'Lead update ' + payload['updated_at']] + [
            source + ': ' + str(payload.get(source, '')) for source in mapping
            if source not in ('conversation_url', 'updated_at')] + [payload['conversation_url']]
            + (['Team notes: ' + payload['notes']] if payload.get('notes') else []))
        if self.hub:
            body = {'properties': {'hs_timestamp': payload['updated_at'],
                    'hs_note_body': html.escape(summary).replace('\n', '<br>')},
                    'associations': [{'to': {'id': contact}, 'types': [
                        {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 202}]}]}
            path = self.prefix + 'objects/notes'
        else:
            body = {'data': [{'Note_Title': 'MarketingAdda lead update', 'Note_Content': summary}]}
            path = self.root + '/' + contact + '/Notes'
        checkpoint(note_submitted=True)
        self.result_id(self.http.request('POST', path, payload=body))
