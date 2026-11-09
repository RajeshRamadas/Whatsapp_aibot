"""Persist lead details with the conversation exchange."""
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import Lead, Membership, Session, User
from . import email
from .analytics import record
from .events import publish_sync

log = logging.getLogger(__name__)


def save_lead(db, conversation, fields: dict) -> bool:
    statement = insert(Lead).values(conversation_id=conversation.id, tenant_id=conversation.tenant_id, fields=fields)
    created = db.execute(statement.on_conflict_do_nothing(index_elements=[Lead.conversation_id])
                         .returning(Lead.conversation_id)).scalar_one_or_none()
    if created is None:
        lead = db.scalar(select(Lead).where(
            Lead.conversation_id == conversation.id, Lead.tenant_id == conversation.tenant_id,
        ).with_for_update())
        if lead is not None:
            lead.fields = {**lead.fields, **fields}
    if created is not None:
        record(db, 'first_lead', conversation.tenant_id)
    return created is not None


def notify_created(tenant_id: str, conversation_id: str) -> None:
    publish_sync(tenant_id, 'lead.created', {'conversation_id': conversation_id})
    with Session() as db:
        recipients = db.scalars(select(User.email).join(Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == tenant_id, Membership.role == 'owner',
        )).all()
    for recipient in recipients:
        try:
            email.sender.send(recipient, 'New Inbox lead', 'Open the workspace Leads page to review it.')
        except Exception:
            log.warning('Lead notification could not be sent')
