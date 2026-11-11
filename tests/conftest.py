import asyncio
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres@127.0.0.1:5433/chatbot_test')
os.environ.setdefault('ADMIN_BOOTSTRAP_TOKEN', 'test-bootstrap')
os.environ.update(WHATSAPP_VERIFY_TOKEN='verify-me', WHATSAPP_APP_SECRET='app-secret',
                  WHATSAPP_ACCESS_TOKEN='wa-token', EMBEDDING_DIMENSIONS='3')

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm, rag, whatsapp  # noqa: E402
from app.api import knowledge  # noqa: E402
from app.api import whatsapp as whatsapp_api
from app.db import Base, InboundEvent, Plan, Session, engine  # noqa: E402
from app.jobs.worker import ingest_source  # noqa: E402
from app.main import app  # noqa: E402

# Tiny fake embedding space: each keyword is an axis.
AXES = ('refund', 'shipping', 'hours')


class FakeEmbedder:
    def embed(self, texts, *, query=False):
        out = []
        for t in texts:
            v = [float(t.lower().count(a)) for a in AXES]
            out.append(v if any(v) else [0.01, 0.01, 0.01])
        return out


class FakeChat:
    def __init__(self, name):
        self.name, self.calls, self.rewrites = name, [], []
        self.tool_turns = []
        self.verifications = []
        self.grounding_verdict = None

    def run(self, model, system, messages, tools):
        if not self.tool_turns:
            return llm.ChatResult(self.complete(model, system, messages), [])
        turns = iter(self.tool_turns)
        results = []

        def turn(enabled):
            text, requests = next(turns)(results, enabled)
            return text, requests, results.extend

        return llm._run(turn, tools)

    def verify(self, model, system, messages):
        self.verifications.append({'model': model, 'system': system, 'messages': messages})
        if self.grounding_verdict is not None:
            if isinstance(self.grounding_verdict, Exception):
                raise self.grounding_verdict
            return json.dumps(self.grounding_verdict)
        payload = json.loads(messages[0]['content'])
        numbers = [s['number'] for s in payload['evidence']]
        return json.dumps({'verdict': 'supported' if numbers else 'non_factual', 'citations': numbers})

    def complete(self, model, system, messages):
        if system == rag.REWRITE_PROMPT:
            # Stand-in for a real rewrite: the first customer message's topic plus the follow-up.
            prompt = messages[-1]['content']
            first = prompt.split('Customer: ', 1)[1].split('\n', 1)[0]
            latest = prompt.rsplit('Latest message: ', 1)[1]
            self.rewrites.append(prompt)
            return f'{latest} (about: {first})'
        self.calls.append({'model': model, 'system': system, 'messages': messages})
        references = re.findall(r'(?m)^\[([0-9]+)\] ', system)
        citations = (' '.join('[' + n + ']' for n in references) + ' ') if references else ''
        return f'{self.name}:{model}: {citations}{messages[-1]["content"]}'


@pytest.fixture
def chats(monkeypatch):
    fakes = {name: FakeChat(name) for name in llm.CHAT_PROVIDERS}
    monkeypatch.setattr(llm, 'chat_provider', lambda name: fakes[name])
    monkeypatch.setattr(llm, 'embedder', lambda: FakeEmbedder())
    return fakes


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(whatsapp, 'send_text', lambda pid, to, body, **kwargs: messages.append((pid, to, body)))
    return messages


@pytest.fixture
def client(chats, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(whatsapp, 'mark_typing', lambda *args: None)
    from sqlalchemy import inspect

    from app.spreadsheets.store import cleanup
    if inspect(engine).has_table('spreadsheet_tables'):
        cleanup(reset=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        db.add(Plan(id='starter', limits={'ai_messages': 1000}))
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
    command.stamp(config, 'head')
    rag.chat_limiter.clear()
    from app.api.auth import _auth_limiter
    _auth_limiter.clear()

    async def inline_ingestion(name, **kwargs):
        if name != 'ingest_source':
            raise ValueError('Unexpected job')
        await asyncio.to_thread(ingest_source_sync, kwargs)
        return kwargs['_job_id']

    def ingest_source_sync(kwargs):
        asyncio.run(ingest_source({'job_try': 3}, kwargs['source_id'], kwargs['tenant_id']))

    monkeypatch.setattr(knowledge, 'enqueue', inline_ingestion)

    async def inline_whatsapp(name, **kwargs):
        if name != 'handle_message':
            raise ValueError('Unexpected job')
        with Session.begin() as db:
            event = db.get(InboundEvent, kwargs['event_id'])
            tenant_id, payload = event.tenant_id, event.payload
        whatsapp.handle(tenant_id, payload)
        with Session.begin() as db:
            db.get(InboundEvent, kwargs['event_id']).status = 'processed'
        return kwargs['_job_id']

    monkeypatch.setattr(whatsapp_api, 'enqueue', inline_whatsapp)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tenant(client):
    r = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                    json={'name': 'Demo Store', 'allowed_origins': ['https://shop.example']})
    assert r.status_code == 200, r.text
    data = r.json()
    data['headers'] = {'X-Admin-Key': data['admin_key']}
    # Existing WhatsApp tests exercise the live assistant unless they explicitly pause it.
    from app.db import Tenant
    with Session.begin() as db:
        db.get(Tenant, data['tenant_id']).bot_live = True
    return data


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(b'app-secret', raw, hashlib.sha256).hexdigest()
    return raw, {'X-Hub-Signature-256': f'sha256={sig}', 'Content-Type': 'application/json'}


def wa_payload(text='hi', msg_id='wamid.1', sender='15551234567', phone_number_id='PNID1', kind='text'):
    msg = {'from': sender, 'id': msg_id, 'timestamp': '1700000000', 'type': kind}
    if kind == 'text':
        msg['text'] = {'body': text}
    return {'object': 'whatsapp_business_account', 'entry': [{'id': 'WABA', 'changes': [{'field': 'messages', 'value': {
        'messaging_product': 'whatsapp',
        'metadata': {'display_phone_number': '1555000', 'phone_number_id': phone_number_id},
        'contacts': [{'wa_id': sender, 'profile': {'name': 'Test'}}], 'messages': [msg]}}]}]}
