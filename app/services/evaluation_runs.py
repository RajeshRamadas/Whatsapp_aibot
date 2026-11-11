"""Accuracy history and one-case worker claims; model calls never hold a DB session."""
import hashlib
import json
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from .. import evaluate, llm
from ..config import DEFAULT_EMBEDDING_MODELS, settings
from ..db import Chunk, EvalCase, QAPair, Session, Source, Tenant, now
from ..models.evaluations import EvalRun
from .appointments import AppointmentError, row
from .audit import audit

BOT = ('name', 'bot_name', 'bot_persona', 'bot_tone', 'bot_rules', 'business_hours', 'timezone',
       'lead_fields', 'default_language', 'bot_messages')
RETRIEVAL = ('embedding_provider', 'embedding_model', 'embedding_dimensions', 'retrieval_top_k',
             'retrieval_candidates', 'retrieval_max_distance', 'text_search_config', 'qa_min_similarity')
FIELDS = ('id', 'label', 'state', 'total', 'completed', 'passed', 'errors', 'accuracy', 'source_hit_rate',
          'last_error', 'created_at', 'started_at', 'finished_at')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def runtime():
    values = {k: getattr(settings, k) for k in RETRIEVAL}
    values['embedding_model'] = settings.embedding_model or DEFAULT_EMBEDDING_MODELS[settings.embedding_provider]
    return values


def knowledge(db, tenant_id):
    sources = [list(r) for r in db.execute(select(Source.id, Source.checksum, Source.status)
               .where(Source.tenant_id == tenant_id).order_by(Source.id))]
    chunks = list(db.scalars(select(Chunk.id).where(Chunk.tenant_id == tenant_id).order_by(Chunk.id)))
    faq = [list(r) for r in db.execute(select(QAPair.id, func.md5(QAPair.question + QAPair.answer))
           .where(QAPair.tenant_id == tenant_id).order_by(QAPair.id))]
    return digest([sources, chunks, faq])


def view(run, detail=False):
    result = {k: getattr(run, k) for k in FIELDS}
    result['context'] = {k: v for k, v in run.context.items() if k not in ('bot', 'request')}
    if detail:
        result.update(cases=run.cases, results=run.results)
    return result


def create(tenant_id, values, actor=('system', None)):
    with Session.begin() as db:
        return create_in_session(db, tenant_id, values, actor)


def create_in_session(db, tenant_id, values, actor=('system', None)):
    request_hash = digest(values | {'request_key': None})
    tenant = db.get(Tenant, tenant_id, with_for_update=True)
    previous = db.scalar(select(EvalRun).where(EvalRun.tenant_id == tenant_id,
                                                EvalRun.request_key == values['request_key']))
    if previous:
        if previous.context['request'] != request_hash:
            raise AppointmentError('This request key was used for different inputs')
        return view(previous)
    if db.scalar(select(EvalRun.id).where(EvalRun.tenant_id == tenant_id,
                                           EvalRun.state.in_(['queued', 'running'])).limit(1)):
        raise AppointmentError('Wait for the active run or cancel it first')
    query = select(EvalCase).where(EvalCase.tenant_id == tenant_id).order_by(EvalCase.created_at, EvalCase.id)
    if values.get('case_ids') is not None:
        query = query.where(EvalCase.id.in_(values['case_ids']))
    cases = db.scalars(query.limit(settings.eval_max_cases_per_run + 1)).all()
    if values.get('case_ids') is not None and len(cases) != len(set(values['case_ids'])):
        raise AppointmentError('Test question not found', 404)
    if not cases:
        raise AppointmentError('Add test questions first', 409)
    if any(not c.expect_no_answer and not (c.expected_keywords or c.expected_source) for c in cases):
        raise AppointmentError('Add scoring expectations to every selected question', 422)
    if len(cases) > settings.eval_max_cases_per_run:
        raise AppointmentError('Select fewer test questions for this run', 422)
    snapshots = [{k: getattr(c, k) for k in ('id', 'question', 'expected_keywords', 'expected_source',
                                           'expect_no_answer')} for c in cases]
    provider, model = llm.resolve_chat(tenant.chat_provider, tenant.chat_model)
    bot = {k: getattr(tenant, k) for k in BOT}
    context = dict(provider=provider, model=model, settings=runtime(),
        knowledge=knowledge(db, tenant_id), case_set=digest(snapshots), bot_hash=digest(bot),
        bot=bot, request=request_hash, scoring='keyword-source-v2')
    run = EvalRun(tenant_id=tenant_id, request_key=values['request_key'], label=values['label'],
                  cases=snapshots, context=context, total=len(cases))
    db.add(run)
    db.flush()
    audit(db, tenant_id, 'eval_run.created', run.id, actor_type=actor[0], actor_user_id=actor[1])
    return view(run)


def listing(tenant_id, limit, offset):
    with Session() as db:
        return [view(r) for r in db.scalars(select(EvalRun).where(EvalRun.tenant_id == tenant_id)
            .order_by(EvalRun.created_at.desc(), EvalRun.id).offset(offset).limit(limit))]


def detail(tenant_id, identity):
    with Session() as db:
        return view(row(db, EvalRun, tenant_id, identity), True)


def cancel(tenant_id, identity, actor=('system', None)):
    with Session.begin() as db:
        run = row(db, EvalRun, tenant_id, identity, lock=True)
        if run.state in ('queued', 'running'):
            run.state, run.finished_at = 'cancelled', now()
            audit(db, tenant_id, 'eval_run.cancelled', identity, actor_type=actor[0], actor_user_id=actor[1])
        return view(run)


def process_one():
    with Session.begin() as db:
        run = db.scalar(select(EvalRun).where(or_(EvalRun.state == 'queued', and_(EvalRun.state == 'running',
            EvalRun.claimed_at < now() - timedelta(minutes=15))))
            .order_by(EvalRun.created_at, EvalRun.id).with_for_update(skip_locked=True).limit(1))
        if not run:
            return False
        tenant_id, identity = run.tenant_id, run.id
        tenant = db.get(Tenant, tenant_id)
        code = ('workspace_suspended' if tenant.suspended else 'configuration_changed' if (
            run.context['settings'] != runtime()
            or run.context['knowledge'] != knowledge(db, tenant_id)) else None)
        if code:
            run.state, run.last_error, run.finished_at = 'failed', code, now()
            return True
        index, stamp = run.completed, now()
        case = EvalCase(**run.cases[index], tenant_id=tenant_id)
        frozen = Tenant(id=tenant_id, **run.context['bot'], chat_provider=run.context['provider'],
                        chat_model=run.context['model'])
        run.state, run.claimed_at = 'running', stamp
        run.started_at = run.started_at or stamp
    # Every case is persisted independently; reclaiming a crashed run skips completed cases.
    report = evaluate.run(frozen, [case])['results'][0]
    report = {**report, 'answer': str(report.get('answer', ''))[:12000]}
    with Session.begin() as db:
        run = row(db, EvalRun, tenant_id, identity, lock=True)
        if run.state != 'running' or run.claimed_at != stamp or run.completed != index:
            return True  # A cancellation or a newer claim owns this run.
        if (run.context['settings'] != runtime()
                or run.context['knowledge'] != knowledge(db, tenant_id)):
            run.state, run.last_error, run.finished_at = 'failed', 'configuration_changed', now()
            return True
        run.results = [*run.results, report]
        run.completed += 1
        summary = evaluate.summarize(run.results)
        for key in ('passed', 'accuracy', 'source_hit_rate'):
            setattr(run, key, summary[key])
        run.errors = sum('error' in r for r in run.results)
        run.state = 'complete' if run.completed == run.total else 'queued'
        if run.state == 'complete':
            run.finished_at = now()
    return True
