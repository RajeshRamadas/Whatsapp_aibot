"""Tenant-scoped review reads and restartable Q&A embedding, without I/O in transactions."""
import random
from datetime import timedelta

from sqlalchemy import or_, select

from .. import llm
from ..db import AnswerReview, Conversation, Message, QAPair, Session, Tenant, WidgetTurn, now
from .audit import audit
from .usage import meter


def questions(tenant_id):
    return select(AnswerReview.id, AnswerReview.question, AnswerReview.unanswered, AnswerReview.state,
        AnswerReview.created_at, AnswerReview.draft_question, AnswerReview.draft_answer, AnswerReview.qa_pair_id,
        AnswerReview.error, Message.content.label('answer'), Message.sources, Conversation.id.label('conversation_id'),
        Conversation.channel, WidgetTurn.feedback).join(Message, Message.id == AnswerReview.message_id).join(
        Conversation, Conversation.id == Message.conversation_id).outerjoin(WidgetTurn,
        (WidgetTurn.id == AnswerReview.widget_turn_id) & (WidgetTurn.conversation_id == Conversation.id)).where(
        AnswerReview.tenant_id == tenant_id, Conversation.tenant_id == tenant_id)


def claim():
    with Session.begin() as db:
        row = db.scalar(select(AnswerReview).where(or_(
            (AnswerReview.state == 'queued') & (AnswerReview.next_attempt_at <= now()),
            (AnswerReview.state == 'processing') & (AnswerReview.claimed_at < now() - timedelta(minutes=20))))
            .order_by(AnswerReview.next_attempt_at, AnswerReview.id).limit(1).with_for_update(skip_locked=True))
        if row is None:
            return None
        if row.attempts >= 5 or db.get(Tenant, row.tenant_id).suspended:
            row.state, row.error = 'failed', 'Publication stopped. Check workspace access and retry.'
            return None
        row.state, row.claimed_at, row.attempts = 'processing', now(), row.attempts + 1
        return row


def process_one():
    snapshot = claim()
    if snapshot is None:
        return False
    try:
        with meter(snapshot.tenant_id):
            vector = llm.embedder().embed([snapshot.draft_question])[0]
        error = None
    except Exception as exc:
        error = ('Embedding is not configured.' if isinstance(exc, llm.ProviderNotConfigured)
                 else 'Could not publish Q&A.')
        vector = None
    with Session.begin() as db:
        row = db.scalar(select(AnswerReview).where(AnswerReview.id == snapshot.id,
            AnswerReview.tenant_id == snapshot.tenant_id, AnswerReview.state == 'processing',
            AnswerReview.attempts == snapshot.attempts, AnswerReview.claimed_at == snapshot.claimed_at)
            .with_for_update())
        if row is None:
            return True  # Deleted conversation or a superseding worker owns the record.
        if db.get(Tenant, row.tenant_id).suspended:
            error = 'Workspace access is paused. Retry after access is restored.'
        if error:
            row.error = error
            row.state = 'failed' if row.attempts >= 5 or error != 'Could not publish Q&A.' else 'queued'
            row.next_attempt_at = now() + timedelta(seconds=30 * 2 ** (row.attempts - 1) + random.uniform(0, 5))
            return True
        pair = db.scalar(select(QAPair).where(QAPair.tenant_id == row.tenant_id, QAPair.id == row.qa_pair_id))
        if pair is None:
            pair = QAPair(tenant_id=row.tenant_id)
            db.add(pair)
        pair.question, pair.answer, pair.embedding = row.draft_question, row.draft_answer, vector
        db.flush()
        row.qa_pair_id, row.state, row.error = pair.id, 'resolved', None
        audit(db, row.tenant_id, 'answer_review.published', row.id, details={'qa_pair_id': pair.id},
              actor_type='system')
    return True
