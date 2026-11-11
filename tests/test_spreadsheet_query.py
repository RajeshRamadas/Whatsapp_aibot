"""Reject untrusted SQL, and prove the role/transaction guards independently."""
import pytest
from sqlalchemy import select

from app.api import knowledge, spreadsheets
from app.api.shared import sha
from app.config import settings
from app.db import ApiKey, Session
from app.spreadsheets import query
from tests.test_spreadsheet_import import tables, upload


@pytest.fixture
def table(client, tenant):
    upload(client, tenant, b'Kind,Price\n2BHK,3500000\n2BHK,3900000\n3BHK,5000000\n2BHK,4500000\n')
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='table-read', key_hash=sha('table-read'),
                     scopes=['viewer']))
    return tables(tenant)[0]


def test_exact_count_sum_and_admin_preview(client, tenant, table):
    path = '/api/v1/tables/' + table.id
    assert client.get('/api/v1/tables', headers=tenant['headers']).json()['tables'][0]['row_count'] == 4
    preview = client.get(path + '/preview', headers=tenant['headers'])
    assert preview.status_code == 200 and preview.json()['columns'] == ['c1', 'c2']
    sql = f"SELECT COUNT(*) AS total, SUM(c2) AS cost FROM {table.table_name} WHERE c1='2BHK' AND c2 < 4000000"
    result = client.post(path + '/query', headers=tenant['headers'], json={'sql': sql})
    assert result.status_code == 200, result.text
    assert result.json()['rows'] == [[2, '7400000']] and result.json()['untrusted']
    with pytest.raises(query.QueryError, match='table_unavailable'):
        query.execute(tenant['tenant_id'], table.id, sql)
    assert client.patch(path, headers=tenant['headers'], json={'enabled': True}).status_code == 200
    assert query.execute(tenant['tenant_id'], table.id, sql)['rows'][0][0] == 2


@pytest.mark.parametrize('sql', [
    'UPDATE {table} SET c2=0', 'DELETE FROM {table}', 'SELECT * INTO stolen FROM {table}',
    'SELECT * FROM {table}; DROP TABLE {table}', 'SELECT pg_read_file(\'/etc/passwd\') FROM {table}',
    'SELECT pg_sleep(2) FROM {table}', 'SELECT * FROM public.tenants',
    'SELECT * FROM {table} JOIN {table} x ON true', 'WITH x AS (DELETE FROM {table}) SELECT * FROM {table}',
    'SELECT _source_id FROM {table}', 'SELECT * FROM {table} FOR UPDATE',
    'SELECT (SELECT count(*) FROM public.tenants) FROM {table}',
    'SELECT * FROM {table} AS t(c1,c2,c3)', 'SELECT c1::regclass FROM {table}',
    'SELECT * FROM {table} LIMIT ALL', 'SELECT * FROM {table} OFFSET 99999999',
    'SELECT count(*) FROM pg_catalog.pg_authid', 'SELECT c1 COLLATE "C" FROM {table}',
])
def test_sql_subset_rejects_unsafe_queries(client, tenant, table, sql):
    with pytest.raises(query.QueryError):
        query.guarded(sql.format(table=table.table_name), table, tenant['tenant_id'], 100)


def test_result_cap_does_not_change_aggregates(client, tenant, table, monkeypatch):
    monkeypatch.setattr(settings, 'spreadsheet_query_row_limit', 2)
    value = query.execute(tenant['tenant_id'], table.id, 'SELECT * FROM ' + table.table_name, admin=True)
    assert len(value['rows']) == 2 and value['truncated']
    value = query.execute(tenant['tenant_id'], table.id, 'SELECT COUNT(*) FROM ' + table.table_name, admin=True)
    assert value['rows'] == [[4]] and not value['truncated']


def test_database_guards_block_mutation_and_enforce_timeout_even_without_parser(client, tenant, table, monkeypatch):
    monkeypatch.setattr(query, 'guarded', lambda sql, *args: (sql, {}))
    with pytest.raises(query.QueryError, match='query_failed'):
        query.execute(tenant['tenant_id'], table.id,
                      f'UPDATE {query.schema(tenant["tenant_id"])}.{table.table_name} SET c2=0', admin=True)
    with pytest.raises(query.QueryError, match='query_failed'):
        query.execute(tenant['tenant_id'], table.id, 'SELECT name FROM public.tenants', admin=True)
    monkeypatch.setattr(settings, 'spreadsheet_query_timeout_ms', 100)
    with pytest.raises(query.QueryError, match='query_timeout'):
        query.execute(tenant['tenant_id'], table.id, 'SELECT pg_sleep(1)', admin=True)
    with Session() as db:
        assert db.scalar(select(ApiKey.id).where(ApiKey.tenant_id == tenant['tenant_id'])) is not None
    # The pooled connection has its original role again after failure.
    assert client.get('/api/v1/tables', headers=tenant['headers']).status_code == 200


def test_endpoints_auth_validation_and_foreign_scope(client, tenant, table, monkeypatch):
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    monkeypatch.setattr(spreadsheets, 'enqueue', knowledge.enqueue)
    base = '/api/v1/tables'
    cases = [('get', base, None), ('get', base + '/' + table.id + '/preview', None),
             ('post', base + '/' + table.id + '/query', {'sql': 'SELECT * FROM ' + table.table_name}),
             ('patch', base + '/' + table.id, {'enabled': True}),
             ('post', base + '/imports/' + table.source_id, None)]
    for method, url, body in cases:
        call = getattr(client, method)
        kwargs = {'json': body} if body else {}
        assert call(url, **kwargs).status_code == 401
        assert call(url, headers={'Authorization': 'Bearer table-read'}, **kwargs).status_code == 403
        if url != base:
            assert call(url, headers=foreign, **kwargs).status_code == 404
            malformed = url.replace(table.id, 'bad').replace(table.source_id, 'bad')
            assert call(malformed, headers=tenant['headers'], **kwargs).status_code == 422
        assert call(url, headers=tenant['headers'], **kwargs).status_code < 300
    assert client.get(base, headers=foreign).json() == {'tables': [], 'sources': []}
    assert client.patch(base + '/' + table.id, headers=tenant['headers'], json={}).status_code == 422
    # Reimport replaced the metadata; old IDs cannot execute a stale query.
    assert client.post(base + '/' + table.id + '/query', headers=tenant['headers'],
                       json={'sql': 'SELECT 1'}).status_code == 404
    new = tables(tenant)[0]
    assert not new.enabled
    assert client.patch(base + '/' + new.id, headers=tenant['headers'], json={}).status_code == 422
    assert client.post(base + '/' + new.id + '/query', headers=tenant['headers'], json={}).status_code == 422


def test_query_literals_are_bound_and_never_embedded_in_statement(client, tenant, table):
    statement, values = query.guarded(f"SELECT c1 FROM {table.table_name} WHERE c1='private-value'",
                                      table, tenant['tenant_id'], 100)
    assert 'private-value' not in statement and 'private-value' in values.values()


def test_reimport_queue_failure_can_be_retried(client, tenant, table, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(spreadsheets, 'enqueue', unavailable)
    path = '/api/v1/tables/imports/' + table.source_id
    assert client.post(path, headers=tenant['headers']).status_code == 503
    catalog = client.get('/api/v1/tables', headers=tenant['headers']).json()
    assert catalog['sources'][0]['status'] == 'failed'
    monkeypatch.setattr(spreadsheets, 'enqueue', knowledge.enqueue)
    assert client.post(path, headers=tenant['headers']).status_code == 202
    assert client.get('/api/v1/tables', headers=tenant['headers']).json()['sources'][0]['status'] == 'ready'


def test_unsupported_sql_and_comments_do_not_leak_into_logs_or_statements(client, tenant, table, caplog):
    with pytest.raises(query.QueryError):
        query.guarded("DO 'private-sensitive-content'", table, tenant['tenant_id'], 100)
    assert 'private-sensitive-content' not in caplog.text
    statement, _ = query.guarded(f'SELECT c1 FROM {table.table_name} /* private-comment */',
                                  table, tenant['tenant_id'], 100)
    assert 'private-comment' not in statement


def test_nonfinite_cast_results_fail_safely(client, tenant, table):
    with pytest.raises(query.QueryError, match='unsupported_result'):
        query.execute(tenant['tenant_id'], table.id,
                      f"SELECT CAST('NaN' AS DOUBLE PRECISION) FROM {table.table_name} LIMIT 1", admin=True)
