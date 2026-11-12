"""Provider changes, deletions, atomic publication, leases and bot access fences."""
import io
from datetime import timedelta

from openpyxl import Workbook
from sqlalchemy import select

from app import rag
from app.db import Chunk, Session, Source, now
from app.integrations.knowledge_sync.http import HTTP, SyncError
from app.integrations.knowledge_sync.providers import Drive, Notion
from app.models.knowledge_sync import KnowledgeConnection as Connection
from app.models.spreadsheets import SpreadsheetTable
from app.services import knowledge_connections as connections
from app.services import knowledge_sync as sync
from app.spreadsheets.query import execute
from tests.test_knowledge_connections import BASE, NOTION, sync_setup  # noqa: F401


def connect(client, headers):
    r = client.post(BASE + '/notion', headers=headers, json=NOTION)
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_notion_refresh_pause_expiry_revoke_and_disconnect(client, tenant, sync_setup, monkeypatch, chats):  # noqa: F811
    headers, _ = sync_setup
    identity = connect(client, headers)
    data = {'version': 'v1', 'body': '# Refunds\n\nRefunds accepted within 30 days.', 'removed': False}
    def fake(self, provider, method, path, **kwargs):
        from app.db import engine
        assert engine.pool.checkedout() == 0
        assert provider == 'notion' and method == 'GET'
        if data['removed']:
            raise SyncError('missing', status=404)
        if path.endswith('/markdown'):
            return {'markdown': data['body'], 'truncated': False, 'unknown_block_ids': []}
        return {'properties': {'title': {'type': 'title', 'title': [{'plain_text': 'Policy'}]}},
                'last_edited_time': data['version']}
    monkeypatch.setattr(HTTP, 'request', fake)
    assert sync.process_one()
    assert not sync.process_one()
    matches = rag.retrieve(tenant['tenant_id'], 'refund')
    assert matches and matches[0].heading == 'Refunds' and '30 days' in matches[0].content
    assert client.get('/api/v1/sources', headers=headers).json() == []
    data.update(version='v2', body='# Refunds\n\nRefunds accepted within 14 days.')
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one()
    assert '14 days' in rag.retrieve(tenant['tenant_id'], 'refund')[0].content
    # Child-block content changes may not alter the parent page's timestamp.
    data['body'] = '# Refunds\n\nRefunds accepted within 7 days.'
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one() and '7 days' in rag.retrieve(tenant['tenant_id'], 'refund')[0].content
    connections.change(tenant['tenant_id'], identity, enabled=False)
    assert not rag.retrieve(tenant['tenant_id'], 'refund') and not sync.process_one()
    connections.change(tenant['tenant_id'], identity, enabled=True)
    assert not rag.retrieve(tenant['tenant_id'], 'refund')
    assert sync.process_one()
    with Session.begin() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        row.last_success = now() - timedelta(days=2)
    assert not rag.retrieve(tenant['tenant_id'], 'refund')
    data['removed'] = True
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one()
    with Session() as db:
        assert not db.scalar(select(Source.id).where(Source.tenant_id == tenant['tenant_id']))
    assert client.delete(BASE + '/' + identity, headers=headers).status_code == 204


def test_drive_exports_changes_and_exact_sheets(client, tenant, sync_setup, monkeypatch):  # noqa: F811
    headers, _ = sync_setup
    import json

    from app.services import crypto
    packet = crypto.encrypt(tenant['tenant_id'], json.dumps({'access_token': 'drive-token',
        'refresh_token': 'drive-refresh', 'expires_at': (now() + timedelta(hours=1)).timestamp()}))
    with Session.begin() as db:
        connection = Connection(tenant_id=tenant['tenant_id'], provider='drive', label='Drive',
            credentials_enc=packet, selection=['rootfolder123'], next_run=now())
        db.add(connection)
        db.flush()
        identity = connection.id
    wb = Workbook()
    ws = wb.active
    ws.append(['Price'])
    ws.append([10])
    ws.append([20])
    stream = io.BytesIO()
    wb.save(stream)
    calls, state = [], {'removed': False}
    def fake(self, provider, method, path, **kwargs):
        from app.db import engine
        assert engine.pool.checkedout() == 0
        calls.append((path, kwargs))
        if path == 'changes/startPageToken':
            return {'startPageToken': 'cursor-1'}
        if path == 'changes':
            return {'changes': [], 'newStartPageToken': 'cursor-2'}
        if path == 'files/rootfolder123':
            return {'mimeType': 'application/vnd.google-apps.folder'}
        if path == 'files':
            return {'files': [] if state['removed'] else [{'id': 'spreadsheet123', 'name': 'Prices', 'version': '1',
                'mimeType': 'application/vnd.google-apps.spreadsheet', 'modifiedTime': '2026-11-12',
                'capabilities': {'canDownload': True}}]}
        assert path == 'files/spreadsheet123/export' and kwargs['params']['mimeType'].endswith('spreadsheetml.sheet')
        return stream.getvalue()
    monkeypatch.setattr(HTTP, 'request', fake)
    assert sync.process_one()
    with Session.begin() as db:
        table = db.scalar(select(SpreadsheetTable).where(SpreadsheetTable.tenant_id == tenant['tenant_id']))
        table.enabled = True
        table_id, table_name, source_id = table.id, table.table_name, table.source_id
    assert execute(tenant['tenant_id'], table_id, 'SELECT SUM(c1) FROM ' + table_name)['rows'] == [['30']]
    assert client.post('/api/v1/tables/imports/' + source_id, headers=headers).status_code == 409
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one()
    assert sum(path.endswith('/export') for path, _ in calls) == 1
    assert any(path == 'changes' for path, _ in calls)
    state['removed'] = True
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one()
    with Session() as db:
        assert not db.scalar(select(SpreadsheetTable.id).where(SpreadsheetTable.tenant_id == tenant['tenant_id']))


def test_partial_scan_and_stale_publication_are_fenced(client, tenant, sync_setup, monkeypatch):  # noqa: F811
    headers, _ = sync_setup
    identity = connect(client, headers)
    def snapshot(self, selection, cursor):
        return [{'id': NOTION['pages'][0], 'name': 'Policy.md', 'format': 'md', 'version': '1',
                 'url': 'https://www.notion.so/test'}], None, None
    monkeypatch.setattr(Notion, 'snapshot', snapshot)
    monkeypatch.setattr(Notion, 'content', lambda *_: b'# Refunds\n\n30 day refund policy')
    assert sync.process_one()
    monkeypatch.setattr(Notion, 'snapshot', lambda *_: (_ for _ in ()).throw(SyncError('incomplete_scan')))
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one()
    assert rag.retrieve(tenant['tenant_id'], 'refund')  # No delete inferred from an incomplete listing.
    with Session.begin() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        row.next_run, row.lease = now() - timedelta(minutes=1), 'expired-worker'
    monkeypatch.setattr(Notion, 'snapshot', snapshot)
    def content(self, item):
        connections.change(tenant['tenant_id'], identity, disconnect=True)
        return b'# Refunds\n\nThis stale result must not publish'
    monkeypatch.setattr(Notion, 'content', content)
    assert sync.process_one()
    with Session() as db:
        assert not db.scalar(select(Chunk.id).where(Chunk.tenant_id == tenant['tenant_id']))


def test_truncated_notion_pages_never_publish(client, tenant, sync_setup, monkeypatch):  # noqa: F811
    headers, _ = sync_setup
    connect(client, headers)
    monkeypatch.setattr(Notion, 'snapshot', lambda *_: ([{'id': NOTION['pages'][0], 'name': 'Policy.md',
        'format': 'md', 'version': '1', 'url': 'https://www.notion.so/test'}], None, None))
    monkeypatch.setattr(HTTP, 'request', lambda *a, **kw: {'markdown': '# Partial', 'truncated': True,
                                                        'unknown_block_ids': [NOTION['pages'][0]]})
    assert sync.process_one()
    with Session() as db:
        assert not db.scalar(select(Source.id).where(Source.tenant_id == tenant['tenant_id']))
        assert db.scalar(select(Connection.last_error).where(Connection.tenant_id == tenant['tenant_id']))


def test_provider_doc_and_slides_exports_and_transport_guards():
    import pytest

    from app.integrations.knowledge_sync.providers import EXPORTS
    assert EXPORTS['application/vnd.google-apps.document'][0] == 'docx'
    assert EXPORTS['application/vnd.google-apps.presentation'] == ('pdf', 'application/pdf')
    for provider, method, path in [('drive', 'DELETE', 'files/id'), ('notion', 'POST', 'pages/id'),
                                    ('drive', 'GET', 'https://evil.example'), ('notion', 'GET', '../pages')]:
        with pytest.raises(SyncError, match='invalid_operation'):
            HTTP().request(provider, method, path)


def test_drive_worker_exchanges_once_and_refreshes_outside_sessions(client, tenant, sync_setup, monkeypatch):  # noqa: F811
    import json
    from urllib.parse import parse_qs, urlsplit

    from app.services import crypto
    headers, _ = sync_setup
    response = client.post(BASE + '/drive', headers=headers, json={'label': 'Drive', 'folders': ['rootfolder123']})
    state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
    assert client.get(BASE + '/callback', params={'state': state, 'code': 'one-use-code'},
                      follow_redirects=False).status_code == 303
    calls = []
    def token(self, provider, method, path, **kwargs):
        from app.db import engine
        assert engine.pool.checkedout() == 0
        calls.append(kwargs['form']['grant_type'])
        return {'access_token': 'new-access', 'refresh_token': 'persistent-refresh', 'expires_in': 3600,
                'scope': connections.SCOPE}
    monkeypatch.setattr(HTTP, 'request', token)
    monkeypatch.setattr(Drive, 'snapshot', lambda *_: ([], 'cursor', set()))
    assert sync.process_one() and calls == ['authorization_code']
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        identity = row.id
        assert row.oauth_secret_enc is None
        private = json.loads(crypto.decrypt(tenant['tenant_id'], row.credentials_enc))
    packet = crypto.encrypt(tenant['tenant_id'], json.dumps(private | {'expires_at': 0}))
    with Session.begin() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        row.credentials_enc = packet
    connections.change(tenant['tenant_id'], identity)
    assert sync.process_one() and calls == ['authorization_code', 'refresh_token']


def test_unknown_notion_markup_is_not_complete_content(monkeypatch):
    import pytest
    monkeypatch.setattr(HTTP, 'request', lambda *a, **kw: {
        'markdown': '# Policy\n<unknown url="https://notion.so/hidden"/>', 'truncated': False,
        'unknown_block_ids': []})
    with pytest.raises(SyncError, match='document_limit'):
        Notion('fake-token').content({'id': NOTION['pages'][0]})


def test_repeat_refresh_preserves_active_lease(client, tenant, sync_setup):  # noqa: F811
    headers, _ = sync_setup
    identity = connect(client, headers)
    with Session.begin() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        row.lease, row.next_run = 'active-worker', now() + timedelta(minutes=15)
        due = row.next_run
    assert client.post(BASE + '/' + identity, headers=headers, json={'action': 'sync'}).status_code == 202
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert row.lease == 'active-worker' and row.next_run == due
    assert client.post(BASE + '/' + identity, headers=headers, json={'action': 'pause'}).status_code == 202
    with Session() as db:
        row = db.scalar(select(Connection).where(Connection.tenant_id == tenant['tenant_id']))
        assert row.lease is None and not row.enabled
