"""Database outbox for crawling: queue downtime and worker termination are recoverable."""
from datetime import timedelta

from sqlalchemy import select

from ..db import Session, Website, now


def pending():
    stamp, result = now(), []
    with Session.begin() as db:
        rows = db.scalars(select(Website).where(
            ((Website.next_run <= stamp) & Website.status.in_(('ready', 'failed', 'queued')) &
             ((Website.status != 'queued') | Website.started_at.is_(None) |
              (Website.started_at < stamp - timedelta(seconds=30)))) |
            ((Website.status == 'running') & (Website.started_at < stamp - timedelta(minutes=70))))
            .order_by(Website.next_run, Website.id).limit(100).with_for_update(skip_locked=True)).all()
        for site in rows:
            if site.status == 'running':
                site.generation = None
                site.attempts += 1
                site.error = 'The crawl worker stopped. Retry this website.'
                if site.attempts >= 5:
                    site.status, site.next_run = 'failed', None
                    continue
            elif site.status != 'queued':
                site.attempts = 0
            site.status, site.next_run, site.started_at = 'queued', stamp, stamp
            result.append((site.id, site.tenant_id, stamp))
    return result  # Queue I/O happens after this transaction has closed.
