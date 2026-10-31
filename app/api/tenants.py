# ruff: noqa: F401
import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select

from .. import evaluate, ingest, llm, rag, whatsapp
from ..config import settings
from ..db import ApiKey, Conversation, EvalCase, Message, Session, Source, Tenant, uid
from ..services.audit import audit
from .principal import Principal, require_role, tenant_for_role
from .shared import (
    UPLOADS,
    WIDGET,
    AgentReply,
    ChatInput,
    CreateTenant,
    EvalCasesInput,
    UpdateTenant,
    admin_tenant,
    eval_view,
    origins,
    sha,
    tenant_view,
)

log = logging.getLogger(__name__)
router = APIRouter()

@router.post('/api/v1/tenants')
def create_tenant(body: CreateTenant, token: str | None = Header(default=None, alias='X-Bootstrap-Token')):
    if not token or not secrets.compare_digest(token.encode(), settings.admin_bootstrap_token.encode()):
        raise HTTPException(401)
    key = 'adm_' + secrets.token_urlsafe(32)
    t = Tenant(id=uid(), name=body.name, admin_key_hash=sha(key), public_key='pub_' + secrets.token_urlsafe(18),
               allowed_origins=json.dumps(body.allowed_origins))
    with Session.begin() as db:
        db.add(t)
        db.flush()
        db.add(ApiKey(id=uid(), tenant_id=t.id, name='Legacy admin key',
                      key_prefix='adm_', key_hash=sha(key), scopes=['owner']))
    return {**tenant_view(t), 'admin_key': key, 'warning': 'Save admin key securely; shown only once'}


@router.get('/api/v1/tenant')
def get_tenant(tenant: Tenant = Depends(tenant_for_role('viewer'))):
    return tenant_view(tenant)


@router.patch('/api/v1/tenant')
def update_tenant(body: UpdateTenant, principal: Principal = Depends(require_role('admin'))):
    tenant = principal.tenant
    changes = body.model_dump(exclude_unset=True)
    if changes.get('name', '') is None:
        del changes['name']
    if changes.get('chat_provider') and changes['chat_provider'] not in llm.CHAT_PROVIDERS:
        raise HTTPException(422, f'chat_provider must be one of {", ".join(llm.CHAT_PROVIDERS)}')
    if 'chat_provider' in changes and 'chat_model' not in changes:
        changes['chat_model'] = None  # a model name rarely carries over between providers
    if 'allowed_origins' in changes:
        changes['allowed_origins'] = json.dumps(changes['allowed_origins'] or [])
    if changes.get('whatsapp_phone_number_id') == '':
        changes['whatsapp_phone_number_id'] = None
    if changes.get('whatsapp_phone_number_id'):
        with Session() as db:
            owner = db.scalar(select(Tenant.id).where(
                Tenant.whatsapp_phone_number_id == changes['whatsapp_phone_number_id']))
        if owner and owner != tenant.id:
            raise HTTPException(409, 'That WhatsApp number is linked to another tenant')
    with Session.begin() as db:
        t = db.get(Tenant, tenant.id)
        for field, value in changes.items():
            setattr(t, field, value)
        if changes:
            audit(db, tenant.id, 'workspace.settings_updated', tenant.id,
                  {'fields': sorted(changes)}, principal.user.id if principal.user else None,
                  'user' if principal.user else 'api_key')
    return tenant_view(t)


