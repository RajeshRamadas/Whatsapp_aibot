"""Bounded, atomic CSV imports and reusable contact/consent selection."""
import csv
import io
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import Contact, Session, now, uid
from .audit import audit

AttributeKey = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,49}$')]
AttributeValue = Annotated[str, StringConstraints(max_length=200)]
Attributes = Annotated[dict[AttributeKey, AttributeValue], Field(max_length=10)]
Language = Annotated[str, StringConstraints(pattern=r'^[a-z]{2,3}(?:[-_][A-Za-z]{2,4})?$')]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SegmentFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: Literal['whatsapp', 'web'] = 'whatsapp'
    language: Language | None = None
    attributes: Attributes = Field(default_factory=dict)
    eligible_only: StrictBool = True


class ImportRow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: Annotated[str, StringConstraints(pattern=r'^\+?[1-9][0-9]{6,14}$')]
    name: Annotated[str, StringConstraints(max_length=200)] | None = None
    language: Language | None = None
    attributes: Attributes = Field(default_factory=dict)
    opt_in_at: datetime | None = None
    opt_in_source: Source | None = None


def eligible():
    return and_(Contact.channel == 'whatsapp', Contact.opt_in_at.is_not(None),
                or_(Contact.opt_out_at.is_(None), Contact.opt_in_at > Contact.opt_out_at))


def marketing_eligible(contact):
    return bool(contact.channel == 'whatsapp' and contact.opt_in_at
                and (contact.opt_out_at is None or contact.opt_in_at > contact.opt_out_at))


def contact_query(tenant_id, filters):
    query = select(Contact).where(Contact.tenant_id == tenant_id, Contact.channel == filters.channel)
    if filters.language:
        query = query.where(Contact.language == filters.language)
    if filters.attributes:
        query = query.where(Contact.attributes.contains(filters.attributes))
    if filters.eligible_only:
        query = query.where(eligible())
    return query


def record_consent(db, contact, opted_in, timestamp, source, *, origin, actor_user_id=None):
    if opted_in:
        contact.opt_in_at = timestamp
    else:
        contact.opt_out_at = timestamp
    contact.consent_source = source
    audit(db, contact.tenant_id, 'contact.opted_in' if opted_in else 'contact.opted_out', contact.id,
          {'origin': origin, 'occurred_at': timestamp.isoformat()}, actor_user_id,
          actor_type='system' if origin == 'whatsapp' else 'user' if actor_user_id else 'api_key')


def parse_csv(content):
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames or []
        if 'phone' not in headers or len(set(headers)) != len(headers) or set(headers) - ImportRow.model_fields.keys():
            raise ValueError('CSV requires phone and supported, unique column names')
        rows, seen = [], set()
        for number, raw in enumerate(reader, start=2):
            if len(rows) >= settings.contact_import_max_rows:
                raise ValueError('CSV exceeds the contact row limit')
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError()
                values = {key: value.strip() for key, value in raw.items() if value.strip()}
                if 'attributes' in values:
                    values['attributes'] = json.loads(values['attributes'])
                if 'opt_in_at' in values:
                    values['opt_in_at'] = datetime.fromisoformat(values['opt_in_at'])
                row = ImportRow.model_validate(values)
                if bool(row.opt_in_at) != bool(row.opt_in_source):
                    raise ValueError()
                if row.opt_in_at and (row.opt_in_at.tzinfo is None or row.opt_in_at > now()):
                    raise ValueError()
                phone = row.phone.removeprefix('+')
                if phone in seen:
                    raise ValueError()
                seen.add(phone)
                row.phone = phone
                rows.append(row)
            except (ValueError, TypeError, ValidationError):
                raise ValueError(f'Invalid CSV row {number}; check phone, consent, attributes and duplicates') from None
        if not rows:
            raise ValueError('CSV must contain at least one contact')
        return sorted(rows, key=lambda row: row.phone)
    except (UnicodeError, csv.Error):
        raise ValueError('Upload a valid UTF-8 CSV file') from None


def import_contacts(content, tenant_id, actor_user_id):
    rows = parse_csv(content)
    incoming = {row.phone: row for row in rows}
    with Session.begin() as db:
        created = db.scalars(insert(Contact).values([
            {'id': uid(), 'tenant_id': tenant_id, 'channel': 'whatsapp', 'external_user': row.phone}
            for row in rows]).on_conflict_do_nothing(index_elements=['tenant_id', 'channel', 'external_user'])
            .returning(Contact.id)).all()
        contacts = db.scalars(select(Contact).where(Contact.tenant_id == tenant_id, Contact.channel == 'whatsapp',
            Contact.external_user.in_(incoming)).order_by(Contact.external_user).with_for_update()).all()
        consent_count = 0
        for contact in contacts:
            row = incoming[contact.external_user]
            if row.name:
                contact.name = row.name
            if row.language:
                contact.language = row.language
            contact.attributes = contact.attributes | row.attributes
            # A bulk file never reverses a recorded STOP, regardless of its claimed consent date.
            if (row.opt_in_at and contact.opt_out_at is None
                    and (contact.opt_in_at is None or row.opt_in_at > contact.opt_in_at)):
                record_consent(db, contact, True, row.opt_in_at, row.opt_in_source,
                               origin='csv', actor_user_id=actor_user_id)
                consent_count += 1
        result = {'processed': len(rows), 'created': len(created), 'updated': len(rows) - len(created),
                  'consents_recorded': consent_count}
        audit(db, tenant_id, 'contacts.imported', details=result, actor_user_id=actor_user_id,
              actor_type='user' if actor_user_id else 'api_key')
    return result
