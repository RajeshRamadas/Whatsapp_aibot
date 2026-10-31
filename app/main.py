import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from .api.routes import UPLOADS, origins, router
from .db import Session, Tenant, init_db
from .services.crypto import validate_configuration

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    UPLOADS.mkdir(exist_ok=True)
    validate_configuration()
    init_db()
    yield


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
