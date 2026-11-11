"""Versioned errors, bounded JSON requests, authenticated cursors and atomic POST replay."""
import base64
import hashlib
import json
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import Session, Tenant, now, uid
from ..models.public_api import ApiRequest
from ..services import crypto


class PublicError(HTTPException):
    def __init__(self, status, code, message, details=None, headers=None):
        super().__init__(status, {'code': code, 'message': message, 'details': details or {}}, headers=headers)


def error_body(exc):
    codes = {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 409: 'conflict', 429: 'rate_limited'}
    detail = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
    return {'error': {'code': detail.get('code', codes.get(exc.status_code, 'request_error')),
            'message': detail.get('message', 'Request failed'), 'details': detail.get('details') or {}}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def cursor(tenant_id, resource, last, filters=None):
    try:
        packet = crypto.encrypt_system('public-cursor', tenant_id + ':' + resource + ':' + digest(filters),
                                       json.dumps({'last': last}).encode())
    except ValueError:
        raise PublicError(503, 'cursor_unavailable', 'Pagination is not configured') from None
    return base64.urlsafe_b64encode(packet).decode().rstrip('=')


def decode_cursor(token, tenant_id, resource, filters=None):
    try:
        data = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        raw = crypto.decrypt_system('public-cursor', tenant_id + ':' + resource + ':' + digest(filters), data)
        last = json.loads(raw)['last']
        if not isinstance(last, (str, int)) or isinstance(last, bool):
            raise ValueError()
        return last
    except (ValueError, InvalidTag, KeyError, TypeError):
        raise PublicError(422, 'invalid_cursor', 'Cursor does not match this workspace, resource or filter') from None


def post(principal, key, route, payload, callback):
    """Callback does database work only. Its mutation and response commit together."""
    hashed, fingerprint = digest(key), digest([route, payload])
    with Session.begin() as db:
        db.get(Tenant, principal.tenant.id, with_for_update=True)
        old = db.scalar(select(ApiRequest).where(ApiRequest.tenant_id == principal.tenant.id,
                                                  ApiRequest.key_hash == hashed).with_for_update())
        if old and old.expires_at > now():
            if old.fingerprint != fingerprint:
                raise PublicError(409, 'idempotency_conflict', 'This key was already used for different inputs')
            return JSONResponse(old.response, status_code=old.status,
                                headers={**old.headers, 'Idempotency-Replayed': 'true'})
        if old:
            db.delete(old)
            db.flush()
        identity = uid()
        try:
            with db.begin_nested():
                status, body, headers = callback(db, identity)
                body = jsonable_encoder(body)
        except HTTPException as exc:
            status, body, headers = exc.status_code, error_body(exc), exc.headers or {}
        except IntegrityError:
            status, body, headers = 409, error_body(PublicError(409, 'resource_conflict',
                                                               'The resource conflicts with an existing record')), {}
        db.add(ApiRequest(id=identity, tenant_id=principal.tenant.id, key_hash=hashed, fingerprint=fingerprint,
            status=status, response=body, headers=headers, expires_at=now() + timedelta(hours=24)))
    return JSONResponse(body, status_code=status, headers={**headers, 'Idempotency-Replayed': 'false'})


class JsonLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        content_type = dict(scope['headers']).get(b'content-type', b'').split(b';')[0].strip().lower()
        if content_type != b'application/json':
            return await JSONResponse(error_body(PublicError(415, 'json_required', 'Use application/json')),
                                      status_code=415)(scope, receive, send)
        chunks, size = [], 0
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            chunk = event.get('body', b'')
            size += len(chunk)
            if size > settings.public_api_max_body_bytes:
                return await JSONResponse(error_body(PublicError(413, 'body_too_large', 'JSON body is too large')),
                                          status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not event.get('more_body', False):
                break
        body = b''.join(chunks)
        async def replay():
            nonlocal body
            if body is not None:
                content, body = body, None
                return {'type': 'http.request', 'body': content, 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)
