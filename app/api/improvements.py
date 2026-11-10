"""Admin-only answer review, feedback metrics and queued human-authored corrections."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, or_, select

from ..db import AnswerReview, Conversation, Session, now
from ..services.audit import audit
from ..services.improvements import questions
from .principal import Principal, require_role
from .qa import PairInput

router = APIRouter(prefix='/api/v1/improvements', tags=['improvements'])


class ReviewView(BaseModel):
    id: str
    question: str
    answer: str
    unanswered: bool
    feedback: Literal['up', 'down'] | None
    state: Literal['open', 'queued', 'processing', 'failed', 'resolved', 'dismissed']
    created_at: datetime
    conversation_id: str
    channel: str
    sources: list[dict]
    draft_question: str | None
    draft_answer: str | None
    qa_pair_id: str | None
    error: str | None


class TopQuestion(BaseModel):
    question: str
    count: int


class ReviewSummary(BaseModel):
    questions: int
    unanswered: int
    positive: int
    negative: int
    satisfaction: float | None
    top_questions: list[TopQuestion]


class ReviewList(BaseModel):
    items: list[ReviewView]
    total: int
    limit: int
    offset: int
    summary: ReviewSummary


class Resolution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: Literal['open', 'dismissed']


class Correction(PairInput):
    model_config = ConfigDict(extra='forbid')


def scoped(db, tenant_id, identity):
    row = db.scalar(select(AnswerReview).where(AnswerReview.id == str(identity),
                    AnswerReview.tenant_id == tenant_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Answer review not found')
    return row


def view(db, tenant_id, identity):
    return ReviewView(**db.execute(questions(tenant_id).where(AnswerReview.id == str(identity))).mappings().one())


def record(db, principal, row, action):
    audit(db, principal.tenant.id, 'answer_review.' + action, row.id,
          actor_user_id=principal.user.id if principal.user else None,
          actor_type='user' if principal.user else 'api_key')


@router.get('', response_model=ReviewList)
def reviews(from_date: date | None = None, to_date: date | None = None,
            channel: Literal['web', 'whatsapp'] | None = None,
            state: Literal['open', 'resolved', 'dismissed', 'all'] = 'open',
            reason: Literal['attention', 'unanswered', 'down', 'all'] = 'attention',
            limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
            principal: Principal = Depends(require_role('admin'))):
    end = to_date or now().date()
    start = from_date or date.fromordinal(max(1, end.toordinal() - 29))
    if end < start or end > now().date() or (end - start).days > 89:
        raise HTTPException(422, 'Choose a past or current inclusive date range of 1–90 days')
    base = questions(principal.tenant.id).where(
        AnswerReview.created_at >= datetime.combine(start, time(), timezone.utc),
        AnswerReview.created_at < datetime.combine(end + timedelta(days=1), time(), timezone.utc))
    if channel:
        base = base.where(Conversation.channel == channel)
    rows = base.subquery()
    query = select(rows)
    if state != 'all':
        query = query.where(rows.c.state.in_(('open', 'queued', 'processing', 'failed')) if state == 'open'
                            else rows.c.state == state)
    if reason != 'all':
        match = {'attention': or_(rows.c.unanswered, rows.c.feedback == 'down'),
                 'unanswered': rows.c.unanswered, 'down': rows.c.feedback == 'down'}
        query = query.where(match[reason])
    def count(condition):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
    with Session() as db:
        totals = db.execute(select(func.count(), count(rows.c.unanswered), count(rows.c.feedback == 'up'),
                                   count(rows.c.feedback == 'down')).select_from(rows)).one()
        normalized = func.lower(func.regexp_replace(func.btrim(rows.c.question), r'\s+', ' ', 'g'))
        top = db.execute(select(func.min(rows.c.question).label('question'), func.count().label('count'))
            .select_from(rows).group_by(normalized).order_by(func.count().desc(), normalized).limit(10))
        top = top.mappings().all()
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.execute(query.order_by(rows.c.created_at.desc(), rows.c.id).offset(offset).limit(limit)).mappings()
        summary = ReviewSummary(questions=totals[0], unanswered=totals[1], positive=totals[2], negative=totals[3],
            satisfaction=100 * totals[2] / (totals[2] + totals[3]) if totals[2] + totals[3] else None,
            top_questions=[TopQuestion(**item) for item in top])
        return ReviewList(items=[ReviewView(**item) for item in items], total=total, limit=limit,
                          offset=offset, summary=summary)


@router.patch('/{review_id}', response_model=ReviewView)
def resolve(review_id: UUID, body: Resolution, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = scoped(db, principal.tenant.id, review_id)
        if row.state in ('queued', 'processing'):
            raise HTTPException(409, 'Wait for Q&A publication to finish.')
        row.state = body.state
        record(db, principal, row, body.state)
        db.flush()
        return view(db, principal.tenant.id, review_id)


@router.post('/{review_id}/answer', response_model=ReviewView, status_code=202)
def correct(review_id: UUID, body: Correction, principal: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        row = scoped(db, principal.tenant.id, review_id)
        same = row.draft_question == body.question and row.draft_answer == body.answer
        if row.state in ('queued', 'processing', 'resolved') and same and (row.state != 'resolved' or row.qa_pair_id):
            return view(db, principal.tenant.id, review_id)
        if row.state in ('queued', 'processing'):
            raise HTTPException(409, 'Wait for Q&A publication to finish.')
        row.draft_question, row.draft_answer = body.question, body.answer
        row.state, row.error, row.attempts, row.next_attempt_at = 'queued', None, 0, now()
        record(db, principal, row, 'queued')
        db.flush()
        return view(db, principal.tenant.id, review_id)
