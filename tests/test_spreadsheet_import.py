"""Real Postgres typed imports, lifecycle and role isolation; fake embeddings only."""
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import select, text

from app import ingest
from app.config import settings
from app.db import Session, Source
from app.models.spreadsheets import SpreadsheetTable
from app.spreadsheets import parse, store

DATA = b'Kind,Price,Active,Available\n2BHK,"35,00,000",true,2026-11-11\n3BHK,5000000,false,\n'


def upload(client, tenant, content=DATA):
    response = client.post('/api/v1/sources', headers=tenant['headers'], files={
        'file': ('plots.csv', content, 'text/csv')})
    assert response.status_code == 202, response.text
    return response.json()['source_id']


def tables(tenant):
    with Session() as db:
        return db.scalars(select(SpreadsheetTable).where(SpreadsheetTable.tenant_id == tenant['tenant_id'])
                          .order_by(SpreadsheetTable.position)).all()


def test_exact_types_and_replacement_pauses_access(client, tenant):
    identity = upload(client, tenant)
    row = tables(tenant)[0]
    assert row.row_count == 2 and not row.enabled
    assert [c['type'] for c in row.columns] == ['text', 'numeric', 'boolean', 'date']
    with Session() as db:
        data = db.execute(text(f'SELECT c2,c3,c4 FROM {store.schema(tenant["tenant_id"])}.{row.table_name} '
                               'ORDER BY c2')).all()
        assert data[0] == (Decimal('3500000'), True, date(2026, 11, 11))
    ingest.ingest(identity, tenant['tenant_id'])
    assert len(tables(tenant)) == 1 and not tables(tenant)[0].enabled


def test_role_can_only_read_its_own_table(client, tenant):
    upload(client, tenant)
    own = tables(tenant)[0]
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    other['headers'] = {'X-Admin-Key': other['admin_key']}
    upload(client, other)
    foreign = tables(other)[0]
    with Session() as db:
        reader = store.role(db, tenant['tenant_id'])
        assert db.scalar(text('SELECT has_table_privilege(:reader,:target,\'SELECT\')'), {
            'reader': reader, 'target': store.schema(tenant['tenant_id']) + '.' + own.table_name})
        assert not db.scalar(text('SELECT has_table_privilege(:reader,:target,\'UPDATE\')'), {
            'reader': reader, 'target': store.schema(tenant['tenant_id']) + '.' + own.table_name})
        assert not db.scalar(text('SELECT has_table_privilege(:reader,:target,\'SELECT\')'), {
            'reader': reader, 'target': store.schema(other['tenant_id']) + '.' + foreign.table_name})
        assert not db.scalar(text('SELECT has_table_privilege(:reader,\'public.tenants\',\'SELECT\')'),
                             {'reader': reader})


def test_source_delete_removes_table_and_cascade_removes_rows(client, tenant):
    identity = upload(client, tenant)
    row = tables(tenant)[0]
    with Session.begin() as db:
        db.delete(db.get(Source, identity))
    with Session() as db:
        assert db.scalar(text(f'SELECT count(*) FROM {store.schema(tenant["tenant_id"])}.{row.table_name}')) == 0
    store.cleanup()
    with Session() as db:
        assert db.scalar(text('SELECT to_regclass(:name)'), {
            'name': store.schema(tenant['tenant_id']) + '.' + row.table_name}) is None
    identity = upload(client, tenant)
    row = tables(tenant)[0]
    assert client.delete('/api/v1/sources/' + identity, headers=tenant['headers']).status_code == 204
    assert tables(tenant) == []
    with Session() as db:
        assert db.scalar(text('SELECT to_regclass(:name)'), {
            'name': store.schema(tenant['tenant_id']) + '.' + row.table_name}) is None


def test_empty_table_and_mixed_values_are_not_silently_numeric(client, tenant, tmp_path):
    upload(client, tenant, b'Kind,Price\n')
    assert tables(tenant)[0].row_count == 0
    path = tmp_path / 'mixed.csv'
    path.write_text('Amount,Blank\n99,\nunknown,\n')
    result = parse.parse(path)[0]
    assert [v['type'] for v in result.columns] == ['text', 'text']
    assert result.rows == [['99', None], ['unknown', None]]


def test_multisheet_workbook_and_formula_rejection(tmp_path):
    book = Workbook()
    book.active.title = 'Homes'
    book.active.append(['Price', 'Price'])
    book.active.append([Decimal('12.50'), 99])
    second = book.create_sheet('Plots')
    second.append(['Label'])
    second.append(['Plot A'])
    path = tmp_path / 'data.xlsx'
    book.save(path)
    result = parse.parse(path)
    assert len(result) == 2 and result[0].rows[0][0] == Decimal('12.5')
    assert [c['name'] for c in result[0].columns] == ['c1', 'c2']
    book.active.append(['=1+1', 1])
    book.save(path)
    with pytest.raises(ValueError, match='formulas'):
        parse.parse(path)


@pytest.mark.parametrize('content', [b'', b'\xff\xfe', b'A\n1,2\n', b'A\n' + b'x' * 2001])
def test_bad_spreadsheets_fail_with_safe_errors(tmp_path, content):
    path = tmp_path / 'data.csv'
    path.write_bytes(content)
    with pytest.raises(ValueError):
        parse.parse(path)


def test_row_limit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'spreadsheet_max_rows', 1)
    path = tmp_path / 'data.csv'
    path.write_text('A\n1\n2\n')
    with pytest.raises(ValueError, match='limit'):
        parse.parse(path)
