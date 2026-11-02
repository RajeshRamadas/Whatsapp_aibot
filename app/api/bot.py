"""Authenticated bot preview. A preview never sends messages or writes conversation history."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import llm, rag
from ..db import Session
from ..services.audit import audit
from .principal import Principal, require_role

log = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/bot', tags=['bot'])


class TestMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post('/test')
def test_bot(body: TestMessage, principal: Principal = Depends(require_role('admin'))):
    try:
        result = rag.answer(principal.tenant, None, body.message)
    except llm.ProviderNotConfigured as exc:
        log.warning('Bot preview provider unavailable: %s', exc)
        raise HTTPException(503, 'Chat provider is not configured') from None
    return {'answer': result.text, 'sources': result.sources}


class LiveChange(BaseModel):
    live: bool
    confirm: bool


@router.post('/live')
def set_live(body: LiveChange, principal: Principal = Depends(require_role('admin'))):
    if not body.confirm:
        raise HTTPException(422, 'Explicit confirmation required')
    with Session.begin() as db:
        tenant = db.get(type(principal.tenant), principal.tenant.id)
        tenant.bot_live = body.live
        audit(db, tenant.id, 'bot.live_changed', tenant.id, {'live': body.live},
              principal.user.id if principal.user else None,
              'user' if principal.user else 'api_key')
    return {'bot_live': body.live}
