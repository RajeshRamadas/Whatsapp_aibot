"""Authenticated bot preview. A preview never sends messages or writes conversation history."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import llm, rag
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
