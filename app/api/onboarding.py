"""Computed, tenant-scoped onboarding state."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ..db import Session, Source
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1', tags=['onboarding'])


@router.get('/onboarding')
def onboarding(principal: Principal = Depends(require_role('viewer'))):
    tenant = principal.tenant
    with Session() as db:
        knowledge_ready = db.scalar(select(func.count(Source.id)).where(
            Source.tenant_id == tenant.id, Source.status == 'ready')) > 0
    return {
        'connect_whatsapp': bool(tenant.whatsapp_phone_number_id),
        'add_knowledge': knowledge_ready,
        'test_bot': False,
        'go_live': False,
    }
