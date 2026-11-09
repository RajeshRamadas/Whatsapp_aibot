"""Correlation and privacy boundaries for logs and error reports."""
import inspect
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid5

import sentry_sdk

from .config import settings
from .db import uid

context = ContextVar('observability', default=None)
IDS = ('request_id', 'tenant_id', 'conversation_id', 'job_id')
EVENTS = {'http.request', 'job.started', 'job.finished', 'job.failed', 'integration.failed',
          'telemetry.unavailable', 'email.queued'}


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def bind(**values):
    if context.get() is not None:
        context.get().update({key: identifier(value) for key, value in values.items() if key in IDS})


@contextmanager
def correlation(**values):
    token = context.set(dict.fromkeys(IDS))
    bind(**values)
    try:
        yield
    finally:
        context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Never interpolate messages/arguments or serialize exception text, URLs or trace locals.
        payload = {'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                   'logger': record.name if re.fullmatch(r'(?:app|arq|uvicorn)(?:\.[a-z_]+)*', record.name)
                   else 'external',
                   'event': record.msg if isinstance(record.msg, str) and record.msg in EVENTS else 'application.log',
                   'line': record.lineno,
                   **(context.get() or dict.fromkeys(IDS))}
        if record.exc_info and record.exc_info[0]:
            name = record.exc_info[0].__name__
            payload['error_type'] = name if re.fullmatch(r'[A-Za-z_]{1,80}', name) else 'Error'
        return json.dumps(payload)


def scrub_event(event, hint):
    hint.pop('attachments', None)
    values = []
    for value in event.get('exception', {}).get('values', []):
        frames = []
        for frame in value.get('stacktrace', {}).get('frames', []):
            module = frame.get('module', '')
            if isinstance(module, str) and re.fullmatch(r'app(?:\.[a-z_]+)+', module):
                frames.append({'filename': module.replace('.', '/') + '.py',
                               'lineno': frame.get('lineno') if isinstance(frame.get('lineno'), int) else None,
                               'in_app': True})
        values.append({'type': 'ApplicationError', 'value': 'Error details removed for privacy',
                       'stacktrace': {'frames': frames}})
    event_id = event.get('event_id') or ''
    return {'event_id': event_id if re.fullmatch(r'[a-f0-9]{32}', event_id) else uid().replace('-', ''),
            'platform': 'python', 'level': 'error', 'exception': {'values': values}, 'tags': {
        key: value for key, value in (context.get() or {}).items() if identifier(value)}}


def configure():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access', 'arq'):
        logger = logging.getLogger(name)
        logger.handlers, logger.propagate = [], True
    if settings.sentry_dsn:
        from sentry_sdk.integrations.dedupe import DedupeIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        sentry_sdk.init(dsn=settings.sentry_dsn, default_integrations=False,
                        integrations=[FastApiIntegration(), StarletteIntegration(), DedupeIntegration()],
                        send_default_pii=False, include_local_variables=False, max_request_body_size='never',
                        max_breadcrumbs=0, traces_sample_rate=0, enable_logs=False, enable_metrics=False,
                        before_send=scrub_event)


@contextmanager
def integration(name):
    from .telemetry import record
    start, outcome = perf_counter(), 'success'
    try:
        yield
    except Exception:
        outcome = 'error'
        logging.getLogger(__name__).error('integration.failed', exc_info=True)
        sentry_sdk.capture_exception()
        raise
    finally:
        record('integration', name, outcome, perf_counter() - start)


def observed_job(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(ctx, *args, **kwargs):
        from arq import Retry

        from .telemetry import record
        values = signature.bind_partial(ctx, *args, **kwargs).arguments
        job_id = ctx.get('job_id') or values.get('job_id') or uid()
        with correlation(job_id=identifier(job_id) or str(uuid5(NAMESPACE_URL, str(job_id))),
                         tenant_id=values.get('tenant_id')):
            start, outcome = perf_counter(), 'success'
            logging.getLogger(__name__).info('job.started')
            try:
                return await function(ctx, *args, **kwargs)
            except Retry:
                outcome = 'retry'
                raise
            except Exception:
                outcome = 'error'
                logging.getLogger(__name__).error('job.failed', exc_info=True)
                sentry_sdk.capture_exception()
                raise
            finally:
                record('job', function.__name__, outcome, perf_counter() - start)
                logging.getLogger(__name__).info('job.finished')
    return wrapped
