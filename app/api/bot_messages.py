"""Editable localized system notices, separate from the user's UI language preference."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select

from ..db import Session, Tenant
from ..services.audit import audit
from ..services.customer_languages import COPY, LANGUAGES, validate_messages
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/bot/messages', tags=['bot'])


class MessageCatalog(BaseModel):
    languages: dict[str, str]
    defaults: dict[str, dict[str, str]]
    overrides: dict[str, dict[str, str]]


class MessageOverrides(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Literal['en', 'hi', 'hi-Latn', 'kn', 'ta', 'te', 'mr', 'bn']
    messages: dict[str, str]

    @field_validator('messages')
    @classmethod
    def safe_templates(cls, value):
        return validate_messages(value)


@router.get('', response_model=MessageCatalog)
def catalog(principal: Principal = Depends(require_role('admin'))):
    return MessageCatalog(languages=LANGUAGES, defaults=COPY, overrides=principal.tenant.bot_messages)


@router.put('', response_model=MessageCatalog)
def update(body: MessageOverrides, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == principal.tenant.id).with_for_update())
        tenant.bot_messages = {**tenant.bot_messages, body.language: body.messages}
        audit(db, tenant.id, 'bot.messages.updated', tenant.id, details={'language': body.language},
              actor_user_id=principal.user.id if principal.user else None,
              actor_type='user' if principal.user else 'api_key')
        return MessageCatalog(languages=LANGUAGES, defaults=COPY, overrides=tenant.bot_messages)
