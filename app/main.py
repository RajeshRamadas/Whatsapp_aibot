import logging
from contextlib import asynccontextmanager
from time import perf_counter

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .api.routes import UPLOADS, origins, router
from .db import Session, Tenant, WebhookFailure, init_db, uid
from .jobs.queue import close_pool
from .observability import configure, correlation, identifier
from .services.analytics import signup_started
from .services.crypto import validate_configuration
from .services.email import validate_configuration as validate_email
from .telemetry import record as telemetry_record

configure()


@asynccontextmanager
async def lifespan(app):
    UPLOADS.mkdir(exist_ok=True)
    validate_configuration()
    validate_email()
    init_db()
    yield
    await close_pool()


app = FastAPI(title='Universal Knowledge Chatbot', version='0.2.0', lifespan=lifespan)


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


app.include_router(router)


@app.middleware('http')
async def request_observability(request: Request, call_next):
    request_id = identifier(request.headers.get('X-Request-Id')) or uid()
    request.state.request_id = request_id
    with correlation(request_id=request_id):
        start, status = perf_counter(), 500
        try:
            response = await call_next(request)
            response.headers['X-Request-Id'] = request_id
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get('route'), 'path', 'unmatched')
            elapsed = perf_counter() - start
            method = (request.method if request.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS')
                      else 'OTHER')
            await run_in_threadpool(telemetry_record, 'http', method + ' ' + route, str(status), elapsed)
            provider = {'/webhooks/whatsapp': 'whatsapp', '/webhooks/razorpay': 'razorpay',
                        '/webhooks/email': 'ses'}.get(route)
            if provider:
                await run_in_threadpool(telemetry_record, 'webhook', provider,
                                        'error' if status >= 400 else 'success', elapsed)
            logging.getLogger(__name__).info('http.request')


@app.middleware('http')
async def signup_attempt(request: Request, call_next):
    if request.method == 'POST' and request.url.path == '/api/v1/auth/signup':
        try:
            await run_in_threadpool(signup_started)
        except SQLAlchemyError:
            logging.getLogger(__name__).warning('Product analytics unavailable')
    return await call_next(request)


def record_webhook_failure(provider, status):
    try:
        with Session.begin() as db:
            db.add(WebhookFailure(provider=provider, http_status=status))
    except SQLAlchemyError:
        logging.getLogger(__name__).warning('Webhook failure history unavailable')


@app.middleware('http')
async def webhook_failure_history(request: Request, call_next):
    providers = {'/webhooks/whatsapp': 'whatsapp', '/webhooks/razorpay': 'razorpay', '/webhooks/email': 'ses'}
    provider = providers.get(request.url.path)
    if provider is None or request.method != 'POST':
        return await call_next(request)
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if status >= 400:
            await run_in_threadpool(record_webhook_failure, provider, status)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', uid())
    with correlation(request_id=request_id):
        sentry_sdk.capture_exception(exc)
    return JSONResponse(status_code=500, headers={'X-Request-Id': request_id},
                        content={'error': {'code': 'internal_error', 'message': 'Something went wrong. Try again.',
                                           'details': None}})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    code = 'http_error'
    if exc.status_code == 401:
        code = 'unauthorized'
    elif exc.status_code == 403:
        code = 'forbidden'
    elif exc.status_code == 404:
        code = 'not_found'
    elif exc.status_code == 409:
        code = 'conflict'
    elif exc.status_code == 429:
        code = 'rate_limited'
    elif exc.status_code >= 500:
        code = 'service_unavailable'
    return JSONResponse(status_code=exc.status_code,
                        content={'error': {'code': code, 'message': str(exc.detail), 'details': None}},
                        headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [{'location': list(e['loc']), 'message': e['msg'], 'type': e['type']}
               for e in exc.errors()]
    return JSONResponse(status_code=422,
                        content={'error': {'code': 'validation_error', 'message': 'Invalid request',
                                           'details': details}})
