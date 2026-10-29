import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqlalchemy import select

from .api.routes import UPLOADS, origins, router
from .db import Session, Tenant, init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    UPLOADS.mkdir(exist_ok=True)
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
