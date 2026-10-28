import hashlib
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import evaluate, ingest, llm, rag, whatsapp
from .config import settings
from .db import Conversation, EvalCase, Message, Session, Source, Tenant, init_db, uid

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

UPLOADS = Path('uploads')
WIDGET = Path(__file__).resolve().parent.parent / 'widget' / 'widget.js'


@asynccontextmanager
async def lifespan(app):
    UPLOADS.mkdir(exist_ok=True)
    init_db()
    yield


app = FastAPI(title='Universal Knowledge Chatbot', version='0.2.0', lifespan=lifespan)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def origins(tenant: Tenant) -> list[str]:
    return json.loads(tenant.allowed_origins)


# Allow only registered website origins for public chat, including preflight requests.
@app.middleware('http')
async def tenant_cors(request: Request, call_next):
    origin = request.headers.get('origin')
    route = request.url.path
    prefix = '/api/v1/chat/'
    allowed = False
    if origin and route.startswith(prefix):
        key = route[len(prefix):].split('/')[0]
        with Session() as db:
            tenant = db.scalar(select(Tenant).where(Tenant.public_key == key))
            allowed = bool(tenant and origin in origins(tenant))
        if request.method == 'OPTIONS':
            if not allowed:
                return Response(status_code=403)
            return Response(status_code=204, headers={
                'Access-Control-Allow-Origin': origin,
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Vary': 'Origin',
            })
    response = await call_next(request)
    if allowed:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    return response


def admin_tenant(key: str | None = Header(default=None, alias='X-Admin-Key')) -> Tenant:
    if not key:
        raise HTTPException(401, 'Missing admin key')
    with Session() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.admin_key_hash == sha(key)))
    if not tenant:
        raise HTTPException(401, 'Invalid admin key')
    return tenant


class CreateTenant(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    allowed_origins: list[str] = Field(default_factory=list)


class UpdateTenant(BaseModel):
    """Only the fields sent are changed. Send null to reset chat_provider / chat_model to the server default."""
    name: str | None = Field(default=None, min_length=2, max_length=200)
    allowed_origins: list[str] | None = None
    chat_provider: str | None = None
    chat_model: str | None = Field(default=None, max_length=100)
    whatsapp_phone_number_id: str | None = Field(default=None, max_length=64)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class EvalCaseInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    expected_keywords: list[str] = Field(default_factory=list, max_length=20)
    expected_source: str | None = Field(default=None, max_length=255)
    expect_no_answer: bool = False


class EvalCasesInput(BaseModel):
    cases: list[EvalCaseInput] = Field(min_length=1, max_length=200)


def eval_view(c: EvalCase) -> dict:
    return {'id': c.id, 'question': c.question, 'expected_keywords': c.expected_keywords,
            'expected_source': c.expected_source, 'expect_no_answer': c.expect_no_answer}


class AgentReply(BaseModel):
    message: str = Field(min_length=1, max_length=whatsapp.MAX_TEXT)


def tenant_view(t: Tenant) -> dict:
    provider, model = llm.resolve_chat(t.chat_provider, t.chat_model)
    return {'tenant_id': t.id, 'name': t.name, 'public_key': t.public_key, 'allowed_origins': origins(t),
            'chat_provider': provider, 'chat_model': model, 'whatsapp_phone_number_id': t.whatsapp_phone_number_id}


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/api/v1/tenants')
def create_tenant(body: CreateTenant, token: str | None = Header(default=None, alias='X-Bootstrap-Token')):
    if not token or not secrets.compare_digest(token.encode(), settings.admin_bootstrap_token.encode()):
        raise HTTPException(401)
    key = 'adm_' + secrets.token_urlsafe(32)
    t = Tenant(id=uid(), name=body.name, admin_key_hash=sha(key), public_key='pub_' + secrets.token_urlsafe(18),
               allowed_origins=json.dumps(body.allowed_origins))
    with Session.begin() as db:
        db.add(t)
    return {**tenant_view(t), 'admin_key': key, 'warning': 'Save admin key securely; shown only once'}


@app.get('/api/v1/tenant')
def get_tenant(tenant: Tenant = Depends(admin_tenant)):
    return tenant_view(tenant)


@app.patch('/api/v1/tenant')
def update_tenant(body: UpdateTenant, tenant: Tenant = Depends(admin_tenant)):
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
    return tenant_view(t)


@app.post('/api/v1/sources', status_code=202)
async def upload(background: BackgroundTasks, file: UploadFile = File(...), tenant: Tenant = Depends(admin_tenant)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ingest.SUPPORTED_SUFFIXES:
        raise HTTPException(415, 'Unsupported file type')
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, 'File too large')
    source_id = uid()
    directory = UPLOADS / tenant.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (source_id + suffix)
    path.write_bytes(content)
    with Session.begin() as db:
        db.add(Source(id=source_id, tenant_id=tenant.id, filename=Path(file.filename or 'file').name[:255],
                      kind=suffix[1:], file_path=str(path), status='queued'))
    background.add_task(ingest.ingest, source_id)  # Dev only: use a durable queue in production.
    return {'source_id': source_id, 'status': 'queued'}


@app.get('/api/v1/sources')
def list_sources(tenant: Tenant = Depends(admin_tenant)):
    with Session() as db:
        sources = db.scalars(select(Source).where(Source.tenant_id == tenant.id).order_by(Source.created_at))
        return [{'id': s.id, 'filename': s.filename, 'status': s.status, 'error': s.error} for s in sources]


@app.delete('/api/v1/sources/{source_id}', status_code=204)
def delete_source(source_id: str, tenant: Tenant = Depends(admin_tenant)):
    with Session.begin() as db:
        src = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant.id))
        if not src:
            raise HTTPException(404, 'Source not found')
        path = Path(src.file_path)
        db.delete(src)  # its chunks go with it (ON DELETE CASCADE)
    path.unlink(missing_ok=True)


@app.post('/api/v1/chat/{public_key}')
def public_chat(public_key: str, body: ChatInput, request: Request):
    with Session() as db:
        t = db.scalar(select(Tenant).where(Tenant.public_key == public_key))
        if not t:
            raise HTTPException(404, 'Unknown chatbot')
        origin = request.headers.get('origin')
        if origin and origin not in origins(t):
            raise HTTPException(403, 'Origin not allowed')
        conv = None
        if body.conversation_id:
            conv = db.scalar(select(Conversation).where(Conversation.id == body.conversation_id,
                                                        Conversation.tenant_id == t.id,
                                                        Conversation.channel == 'web'))
            if not conv:
                raise HTTPException(404, 'Conversation not found')
    # The public key is an identifier, not a secret, so requests without an Origin
    # (scripts, curl) are allowed too: the rate limit is what bounds abuse.
    client = request.client.host if request.client else 'unknown'
    if not rag.chat_limiter.allow(f'web:{t.id}:{client}'):
        raise HTTPException(429, 'Too many messages. Please wait a minute.')
    try:
        result = rag.answer(t, conv.id if conv else None, body.message)
    except llm.ProviderNotConfigured as e:
        log.error('Chat provider not configured: %s', e)
        raise HTTPException(503, 'The chatbot is not configured yet') from None
    is_new = conv is None
    conv = conv or Conversation(id=uid(), tenant_id=t.id, channel='web')
    # Saved only after a successful answer, so failed requests leave nothing behind.
    rag.save_exchange(conv, ('user', body.message), ('assistant', result.text), create=is_new)
    return {'conversation_id': conv.id, 'answer': result.text, 'sources': result.sources}


@app.get('/api/v1/conversations')
def list_conversations(handoff: bool | None = None, limit: int = Query(50, ge=1, le=200),
                       tenant: Tenant = Depends(admin_tenant)):
    query = select(Conversation).where(Conversation.tenant_id == tenant.id)
    if handoff is not None:
        query = query.where(Conversation.handoff == handoff)
    with Session() as db:
        rows = db.scalars(query.order_by(Conversation.created_at.desc()).limit(limit)).all()
    return [{'id': c.id, 'channel': c.channel, 'user': c.external_user, 'handoff': c.handoff,
             'created_at': c.created_at} for c in rows]


def tenant_conversation(db, tenant: Tenant, conversation_id: str) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                Conversation.tenant_id == tenant.id))
    if not conv:
        raise HTTPException(404, 'Conversation not found')
    return conv


@app.get('/api/v1/conversations/{conversation_id}/messages')
def conversation_messages(conversation_id: str, tenant: Tenant = Depends(admin_tenant)):
    with Session() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
        rows = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
    return [{'role': m.role, 'content': m.content, 'created_at': m.created_at} for m in rows]


@app.post('/api/v1/conversations/{conversation_id}/handoff')
def set_handoff(conversation_id: str, active: bool = True, tenant: Tenant = Depends(admin_tenant)):
    """active=true: a human takes over and the bot stops replying. active=false: hand back to the bot."""
    with Session.begin() as db:
        tenant_conversation(db, tenant, conversation_id).handoff = active
    return {'id': conversation_id, 'handoff': active}


@app.post('/api/v1/conversations/{conversation_id}/reply')
def agent_reply(conversation_id: str, body: AgentReply, tenant: Tenant = Depends(admin_tenant)):
    """Send a message from a human agent to a WhatsApp user."""
    with Session() as db:
        conv = tenant_conversation(db, tenant, conversation_id)
    if conv.channel != 'whatsapp' or not tenant.whatsapp_phone_number_id:
        raise HTTPException(409, 'Agent replies are only available for WhatsApp conversations')
    if not settings.whatsapp_enabled:
        raise HTTPException(503, 'WhatsApp is not configured')
    # Meta only delivers free-form messages within 24 hours of the user's last message.
    whatsapp.send_text(tenant.whatsapp_phone_number_id, conv.external_user, body.message)
    rag.save_exchange(conv, ('agent', body.message))
    return {'status': 'sent'}


@app.post('/api/v1/evals', status_code=201)
def add_eval_cases(body: EvalCasesInput, tenant: Tenant = Depends(admin_tenant)):
    cases = [EvalCase(tenant_id=tenant.id, **c.model_dump()) for c in body.cases]
    with Session.begin() as db:
        db.add_all(cases)
    return [eval_view(c) for c in cases]


@app.get('/api/v1/evals')
def list_eval_cases(tenant: Tenant = Depends(admin_tenant)):
    with Session() as db:
        return [eval_view(c) for c in db.scalars(select(EvalCase).where(EvalCase.tenant_id == tenant.id)
                                                  .order_by(EvalCase.created_at))]


@app.delete('/api/v1/evals/{case_id}', status_code=204)
def delete_eval_case(case_id: str, tenant: Tenant = Depends(admin_tenant)):
    with Session.begin() as db:
        case = db.scalar(select(EvalCase).where(EvalCase.id == case_id, EvalCase.tenant_id == tenant.id))
        if not case:
            raise HTTPException(404, 'Test question not found')
        db.delete(case)


@app.post('/api/v1/evals/run')
def run_evals(tenant: Tenant = Depends(admin_tenant)):
    """Ask every test question and score the answers. Each question is a real (billed) provider call."""
    with Session() as db:
        cases = db.scalars(select(EvalCase).where(EvalCase.tenant_id == tenant.id)
                           .order_by(EvalCase.created_at)).all()
    if not cases:
        raise HTTPException(409, 'Add test questions first (POST /api/v1/evals)')
    return evaluate.run(tenant, cases)


@app.get('/webhooks/whatsapp')
def whatsapp_verify(mode: str = Query('', alias='hub.mode'), token: str = Query('', alias='hub.verify_token'),
                    challenge: str = Query('', alias='hub.challenge')):
    if not settings.whatsapp_enabled:
        raise HTTPException(404)
    if mode == 'subscribe' and secrets.compare_digest(token.encode(), settings.whatsapp_verify_token.encode()):
        return PlainTextResponse(challenge)
    raise HTTPException(403)


@app.post('/webhooks/whatsapp')
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    if not settings.whatsapp_enabled:
        raise HTTPException(404)
    body = await request.body()
    if not whatsapp.valid_signature(body, request.headers.get('x-hub-signature-256')):
        raise HTTPException(401, 'Invalid signature')
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, 'Invalid JSON') from None
    for message in whatsapp.parse_messages(payload):
        tenant_id = await run_in_threadpool(whatsapp.accept, message)
        if tenant_id:
            background.add_task(whatsapp.handle, tenant_id, message)  # Dev only: use a durable queue in production.
    return {'status': 'ok'}


@app.get('/widget.js')
def widget_js():
    return FileResponse(WIDGET, media_type='application/javascript')
