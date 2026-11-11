"""Additive public v1 contract; legacy dashboard endpoints retain their existing representations."""
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .auth import PublicPrincipal, authenticate
from .contracts import JsonLimit, error_body

app = FastAPI(title='MarketingAdda Public API', version='1.0.0',
    servers=[{'url': '/api/v1/public'}, {'url': '/api/backend/public', 'description': 'Dashboard proxy'}])
app.add_middleware(JsonLimit)


@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['API-Version'] = '1'
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(error_body(exc), status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'error': {'code': 'validation_error', 'message': 'Invalid request', 'details': {
        'fields': [{'location': list(e['loc']), 'message': e['msg'], 'type': e['type']} for e in exc.errors()]}}},
        status_code=422)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc):
    logging.getLogger(__name__).error('Public API request failed (%s)', type(exc).__name__)
    return JSONResponse({'error': {'code': 'internal_error', 'message': 'Request could not be completed',
                                   'details': {}}}, status_code=500)


@app.get('/capabilities', tags=['contract'])
def capabilities(p: PublicPrincipal = Depends(authenticate)):
    return {'version': '1', 'workspace_id': p.tenant.id, 'scopes': sorted(p.scopes),
            'requests_per_minute': p.limit, 'max_page_size': 100, 'idempotency_hours': 24}

from .resources import router  # noqa: E402

app.include_router(router)
