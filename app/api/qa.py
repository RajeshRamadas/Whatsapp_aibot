"""Manage curated workspace FAQ entries."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select

from .. import llm
from ..db import QAPair, Session, Tenant
from ..services.usage import meter
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1/qa-pairs')


class PairInput(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


def view(pair):
    return {'id': pair.id, 'question': pair.question, 'answer': pair.answer}


def scoped_pair(db, tenant_id, pair_id):
    pair = db.scalar(select(QAPair).where(QAPair.id == pair_id, QAPair.tenant_id == tenant_id))
    if pair is None:
        raise HTTPException(404, 'FAQ entry not found')
    return pair


def embed(question, tenant_id):
    try:
        with meter(tenant_id):
            return llm.embedder().embed([question])[0]
    except llm.ProviderNotConfigured as exc:
        raise HTTPException(503, 'Embeddings are not configured') from exc


@router.get('')
def list_pairs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        return [view(pair) for pair in db.scalars(select(QAPair).where(QAPair.tenant_id == tenant.id)
                                                  .order_by(QAPair.id).offset(offset).limit(limit))]


@router.post('', status_code=201)
def create_pair(body: PairInput, tenant: Tenant = Depends(tenant_for_role('admin'))):
    vector = embed(body.question, tenant.id)
    with Session.begin() as db:
        pair = QAPair(tenant_id=tenant.id, question=body.question, answer=body.answer, embedding=vector)
        db.add(pair)
        db.flush()
        return view(pair)


@router.get('/{pair_id}')
def get_pair(pair_id: str, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        return view(scoped_pair(db, tenant.id, pair_id))


@router.put('/{pair_id}')
def update_pair(pair_id: str, body: PairInput, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session() as db:
        scoped_pair(db, tenant.id, pair_id)
    vector = embed(body.question, tenant.id)
    with Session.begin() as db:
        pair = scoped_pair(db, tenant.id, pair_id)
        pair.question, pair.answer, pair.embedding = body.question, body.answer, vector
        return view(pair)


@router.delete('/{pair_id}', status_code=204)
def delete_pair(pair_id: str, tenant: Tenant = Depends(tenant_for_role('admin'))):
    with Session.begin() as db:
        db.delete(scoped_pair(db, tenant.id, pair_id))
