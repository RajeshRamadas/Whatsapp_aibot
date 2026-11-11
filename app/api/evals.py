"""Workspace test questions and durable accuracy runs; providers execute only in the worker."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from ..config import settings
from ..db import EvalCase, Session, uid
from ..services import evaluation_runs as service
from ..services.appointments import AppointmentError
from .appointments import Confirm
from .principal import Principal, require_role
from .shared import EvalCaseInput, EvalCasesInput, eval_view

router = APIRouter(prefix='/api/v1/evals', tags=['accuracy'])


class CaseView(BaseModel):
    id: str
    question: str
    expected_keywords: list[str]
    expected_source: str | None
    expect_no_answer: bool


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = 'Accuracy run'
    case_ids: list[UUID] | None = Field(None, min_length=1, max_length=200)
    request_key: UUID | None = None


class RunSummary(BaseModel):
    id: str
    label: str
    state: str
    total: int
    completed: int
    passed: int
    errors: int
    accuracy: float | None
    source_hit_rate: float | None
    last_error: str | None
    context: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvalResult(BaseModel):
    id: str
    question: str
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    missing_keywords: list[str] = Field(default_factory=list)
    answer: str = ''
    sources: list[str] = Field(default_factory=list)
    error: str | None = None


class RunDetail(RunSummary):
    cases: list[CaseView]
    results: list[EvalResult]


class AccuracyCases(BaseModel):
    cases: list[CaseView]
    max_run_cases: int


def actor(p):
    return ('user', p.user.id) if p.user else ('api_key', None)


def run(p, operation, *args, **kwargs):
    try:
        return operation(p.tenant.id, *args, **kwargs)
    except AppointmentError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.post('', response_model=list[CaseView], status_code=201)
def add_eval_cases(body: EvalCasesInput, p: Principal = Depends(require_role('admin'))):
    cases = [EvalCase(tenant_id=p.tenant.id, **c.model_dump()) for c in body.cases]
    with Session.begin() as db:
        db.add_all(cases)
    return [eval_view(c) for c in cases]


@router.get('', response_model=list[CaseView])
def list_eval_cases(limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
                    p: Principal = Depends(require_role('viewer'))):
    with Session() as db:
        return [eval_view(c) for c in db.scalars(select(EvalCase).where(EvalCase.tenant_id == p.tenant.id)
            .order_by(EvalCase.created_at, EvalCase.id).offset(offset).limit(limit))]


@router.get('/cases', response_model=AccuracyCases)
def case_catalog(p: Principal = Depends(require_role('viewer'))):
    return {'cases': list_eval_cases(500, 0, p), 'max_run_cases': settings.eval_max_cases_per_run}


@router.delete('/{case_id}', status_code=204)
def delete_eval_case(case_id: UUID, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        case = db.scalar(select(EvalCase).where(EvalCase.id == str(case_id), EvalCase.tenant_id == p.tenant.id))
        if not case:
            raise HTTPException(404, 'Test question not found')
        db.delete(case)


@router.put('/{case_id}', response_model=CaseView)
def update_case(case_id: UUID, body: EvalCaseInput, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        case = db.scalar(select(EvalCase).where(EvalCase.id == str(case_id), EvalCase.tenant_id == p.tenant.id)
                         .with_for_update())
        if not case:
            raise HTTPException(404, 'Test question not found')
        for key, value in body.model_dump().items():
            setattr(case, key, value)
        return eval_view(case)


@router.post('/run', response_model=RunSummary, status_code=202)
def start_run(body: RunInput | None = None, p: Principal = Depends(require_role('admin'))):
    values = (body or RunInput()).model_dump(mode='json')
    values['request_key'] = values['request_key'] or uid()
    return run(p, service.create, values, actor=actor(p))


@router.get('/runs', response_model=list[RunSummary])
def list_runs(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
              p: Principal = Depends(require_role('viewer'))):
    return service.listing(p.tenant.id, limit, offset)


@router.get('/runs/{identity}', response_model=RunDetail)
def get_run(identity: UUID, p: Principal = Depends(require_role('viewer'))):
    return run(p, service.detail, str(identity))


@router.post('/runs/{identity}/cancel', response_model=RunSummary)
def cancel_run(identity: UUID, body: Confirm, p: Principal = Depends(require_role('admin'))):
    return run(p, service.cancel, str(identity), actor=actor(p))
