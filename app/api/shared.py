# ruff: noqa: F401
import hashlib
import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import evaluate, ingest, llm, rag, whatsapp
from ..config import settings
from ..db import Conversation, EvalCase, Message, Session, Source, Tenant, uid

log = logging.getLogger(__name__)
UPLOADS = Path('uploads')
WIDGET = Path(__file__).resolve().parents[2] / 'widget' / 'widget.js'

def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def origins(tenant: Tenant) -> list[str]:
    return json.loads(tenant.allowed_origins)


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


