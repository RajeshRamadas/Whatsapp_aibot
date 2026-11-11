"""Knowledge writes are durable operations; handlers never embed or access storage."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from sqlalchemy import select

from ..api.qa import PairInput
from ..db import QAPair, Session, Source, uid
from ..models.public_api import ApiOperation
from .auth import PublicPrincipal, require
from .contracts import PublicError, post
from .resources import Key, owned

router = APIRouter(tags=['knowledge operations'])


class OperationView(BaseModel):
    id: str
    kind: str
    resource_id: str
    state: str
    attempts: int
    last_error: str | None
    created_at: datetime


def operation_view(row):
    return {key: getattr(row, key) for key in OperationView.model_fields}


class SourceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9 _.-]{0,240}\.(?:txt|md)$')]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500000)]

    @model_validator(mode='after')
    def safe_filename(self):
        if '..' in self.filename:
            raise ValueError('Filename must not contain parent path segments')
        return self


class QAInput(PairInput):
    model_config = ConfigDict(extra='forbid')


def queue(db, p, identity, kind, resource_id, payload):
    if db.scalar(select(ApiOperation.id).where(ApiOperation.tenant_id == p.tenant.id,
            ApiOperation.resource_id == resource_id, ApiOperation.state.in_(['queued', 'running']))):
        raise PublicError(409, 'operation_pending', 'Wait for the current operation to finish')
    row = ApiOperation(id=identity, tenant_id=p.tenant.id, key_id=p.key_id, kind=kind,
                       resource_id=resource_id, payload=payload)
    db.add(row)
    db.flush()
    return 202, operation_view(row), {'Location': '/api/v1/public/operations/' + identity}


@router.get('/operations/{identity}', response_model=OperationView)
def operation(identity: UUID, p: PublicPrincipal = Depends(require('knowledge:read'))):
    with Session() as db:
        return operation_view(owned(db, ApiOperation, p.tenant.id, identity))


@router.post('/sources', response_model=OperationView, status_code=202)
def source(body: SourceInput, key: Key, p: PublicPrincipal = Depends(require('knowledge:write'))):
    def save(db, identity):
        suffix, resource_id = body.filename.rsplit('.', 1)[1], uid()
        storage_key = f'{p.tenant.id}/public/{resource_id}.{suffix}'
        db.add(Source(id=resource_id, tenant_id=p.tenant.id, filename=body.filename,
                      kind=suffix, status='queued', file_path='storage:' + storage_key))
        return queue(db, p, identity, 'source.create', resource_id,
                     {'storage_key': storage_key, 'content': body.content})
    return post(p, key, '/sources', body.model_dump(), save)


@router.post('/qa-pairs', response_model=OperationView, status_code=202)
def qa(body: QAInput, key: Key, p: PublicPrincipal = Depends(require('knowledge:write'))):
    return post(p, key, '/qa-pairs', body.model_dump(),
                lambda db, identity: queue(db, p, identity, 'qa.create', uid(), body.model_dump()))


@router.post('/qa-pairs/{identity}/updates', response_model=OperationView, status_code=202)
def update_qa(identity: UUID, body: QAInput, key: Key, p: PublicPrincipal = Depends(require('knowledge:write'))):
    def save(db, operation_id):
        owned(db, QAPair, p.tenant.id, identity, lock=True)
        return queue(db, p, operation_id, 'qa.update', str(identity), body.model_dump())
    return post(p, key, '/qa-pairs/' + str(identity) + '/updates', body.model_dump(), save)


@router.delete('/qa-pairs/{identity}', status_code=204)
def delete_qa(identity: UUID, p: PublicPrincipal = Depends(require('knowledge:write'))):
    with Session.begin() as db:
        row = owned(db, QAPair, p.tenant.id, identity, lock=True)
        if db.scalar(select(ApiOperation.id).where(ApiOperation.tenant_id == p.tenant.id,
                ApiOperation.resource_id == str(identity), ApiOperation.state.in_(['queued', 'running']))):
            raise PublicError(409, 'operation_pending', 'Wait for the current operation to finish')
        db.delete(row)


@router.delete('/sources/{identity}', status_code=204)
def delete_source(identity: UUID, p: PublicPrincipal = Depends(require('knowledge:write'))):
    with Session.begin() as db:
        # Same tenant lock order as POST replay avoids queue/delete races.
        from ..db import Tenant
        db.get(Tenant, p.tenant.id, with_for_update=True)
        row = owned(db, Source, p.tenant.id, identity, lock=True)
        from ..spreadsheets.store import remove
        remove(db, p.tenant.id, str(identity))
        if row.file_path.startswith('storage:'):
            queue(db, p, uid(), 'source.delete', str(identity), {'storage_key': row.file_path[8:]})
        db.delete(row)
