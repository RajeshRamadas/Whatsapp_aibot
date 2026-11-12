"""Owner/admin previews and publication for exact spreadsheet answers."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..db import Session, Source, Tenant, uid
from ..jobs.queue import enqueue
from ..models.spreadsheets import SpreadsheetTable
from ..services.audit import audit
from ..services.knowledge_sync import usable as sync_usable
from ..spreadsheets.query import QueryError, execute, owned
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/tables', tags=['spreadsheet-tables'])


class ColumnView(BaseModel):
    name: str
    label: str
    type: str
    currency: str | None = None


class TableView(BaseModel):
    id: str
    source_id: str
    title: str
    table_name: str
    columns: list[ColumnView]
    row_count: int
    enabled: bool


class SheetSourceView(BaseModel):
    sync_connection_id: str | None = None
    id: str
    filename: str
    status: str
    error: str | None


class CatalogView(BaseModel):
    tables: list[TableView]
    sources: list[SheetSourceView]


class TableEnable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


class QueryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sql: str = Field(min_length=1, max_length=4000)


class TableCitation(BaseModel):
    id: str
    filename: str
    heading: str


class QueryView(BaseModel):
    columns: list[str]
    rows: list[list[object]]
    truncated: bool
    untrusted: bool
    source: TableCitation


def view(row):
    return {name: getattr(row, name) for name in TableView.model_fields}


def failure(exc):
    return HTTPException(exc.status, {'code': exc.code, 'message': 'The table query could not be completed safely.'})


@router.get('', response_model=CatalogView)
def catalog(response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    with Session() as db:
        sources = list(db.scalars(select(Source).where(Source.tenant_id == p.tenant.id,
            Source.kind.in_(['csv', 'xlsx']), sync_usable()).order_by(Source.filename)))
        tables = db.scalars(select(SpreadsheetTable).where(SpreadsheetTable.tenant_id == p.tenant.id)
                            .order_by(SpreadsheetTable.source_id, SpreadsheetTable.position))
        return {'tables': [view(row) for row in tables],
                'sources': [{name: getattr(s, name) for name in SheetSourceView.model_fields} for s in sources]}


@router.patch('/{identity}', response_model=TableView)
def enable(identity: UUID, body: TableEnable, p: Principal = Depends(require_role('admin'))):
    try:
        with Session.begin() as db:
            db.get(Tenant, p.tenant.id, with_for_update=True)
            row, source = owned(db, p.tenant.id, identity)
            if body.enabled and source.status != 'ready':
                raise QueryError('table_unavailable', 409)
            row.enabled = body.enabled
            audit(db, p.tenant.id, 'spreadsheet.enabled' if body.enabled else 'spreadsheet.paused', row.id,
                  actor_user_id=p.user.id if p.user else None)
            return view(row)
    except QueryError as exc:
        raise failure(exc) from None


@router.post('/{identity}/query', response_model=QueryView)
def query(identity: UUID, body: QueryInput, response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return execute(p.tenant.id, str(identity), body.sql, admin=True)
    except QueryError as exc:
        raise failure(exc) from None


@router.get('/{identity}/preview', response_model=QueryView)
def preview(identity: UUID, response: Response, p: Principal = Depends(require_role('admin'))):
    response.headers['Cache-Control'] = 'no-store'
    try:
        with Session() as db:
            row, _ = owned(db, p.tenant.id, identity)
        return execute(p.tenant.id, str(identity), 'SELECT * FROM ' + row.table_name, admin=True, limit=20)
    except QueryError as exc:
        raise failure(exc) from None


@router.post('/imports/{identity}', response_model=SheetSourceView, status_code=202)
async def reimport(identity: UUID, p: Principal = Depends(require_role('admin'))):
    with Session.begin() as db:
        db.get(Tenant, p.tenant.id, with_for_update=True)
        row = db.scalar(select(Source).where(Source.id == str(identity), Source.tenant_id == p.tenant.id)
                        .with_for_update())
        if row is None:
            raise HTTPException(404, 'Spreadsheet not found')
        if row.sync_connection_id:
            raise HTTPException(409, 'Refresh this file from Knowledge sync')
        if row.kind not in ('csv', 'xlsx'):
            raise HTTPException(422, 'Choose a CSV or XLSX source')
        if row.status not in ('ready', 'failed'):
            raise HTTPException(409, 'Wait for the existing import to finish')
        row.status, row.error = 'queued', None
        for table in db.scalars(select(SpreadsheetTable).where(SpreadsheetTable.tenant_id == p.tenant.id,
                               SpreadsheetTable.source_id == row.id)):
            table.enabled = False
        audit(db, p.tenant.id, 'spreadsheet.reimported', row.id, actor_user_id=p.user.id if p.user else None)
    try:
        await enqueue('ingest_source', source_id=str(identity), tenant_id=p.tenant.id, _job_id='tables:' + uid())
    except (OSError, ConnectionError, TimeoutError, RuntimeError):
        with Session.begin() as db:
            current = db.scalar(select(Source).where(Source.id == str(identity), Source.tenant_id == p.tenant.id)
                                .with_for_update())
            if current and current.status == 'queued':
                current.status, current.error = 'failed', 'Background queue unavailable. Retry the spreadsheet import.'
        raise HTTPException(503, 'Background queue unavailable') from None
    return {name: getattr(row, name) for name in SheetSourceView.model_fields}
