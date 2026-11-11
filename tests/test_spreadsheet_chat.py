"""Exact table facts flow through real guarded SQL and receive source citations."""
from app import llm, rag
from app.db import Session, Tenant, engine
from app.spreadsheets import chat
from tests.test_spreadsheet_import import tables, upload


def test_chat_discovers_tables_and_queries_exact_count(client, tenant, chats, monkeypatch):
    upload(client, tenant, b'Kind,Price\n2BHK,3500000\n2BHK,3900000\n2BHK,4500000\n3BHK,2000000\n')
    table = tables(tenant)[0]
    assert not rag.retrieve(tenant['tenant_id'], '2BHK', [1, 0, 0])
    registry, _, _ = chat.tools(tenant['tenant_id'], 1)
    assert registry == []
    client.patch('/api/v1/tables/' + table.id, headers=tenant['headers'], json={'enabled': True})
    def run(model, system, messages, tools):
        assert engine.pool.checkedout() == 0 and 'UNTRUSTED DATA' in system and 'COUNT/SUM' in system
        registry = {t.name: t for t in tools}
        catalog = registry['list_tables'].handler({'search': 'Price'})
        assert len(catalog['tables']) == 1
        selected = catalog['tables'][0]
        result = registry['query_table'].handler({'table_id': selected['id'],
            'sql': f"SELECT COUNT(*) AS total FROM {selected['table_name']} WHERE c1='2BHK' AND c2 < 4000000"})
        assert result['rows'] == [[2]] and result['source']['number'] == 1
        return llm.ChatResult('There are 2 matching plots. [1]', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    answer = rag.answer(workspace, None, 'How many 2BHK plots under ₹40 lakh?')
    assert answer.text == 'There are 2 matching plots. [1]'
    assert answer.sources == [{'number': 1, 'filename': 'plots.csv', 'heading': 'CSV', 'page': None}]
    assert not answer.uncertain


def test_failed_table_query_cannot_be_replaced_by_a_guessed_answer(client, tenant, chats, monkeypatch):
    upload(client, tenant)
    table = tables(tenant)[0]
    client.patch('/api/v1/tables/' + table.id, headers=tenant['headers'], json={'enabled': True})
    def run(model, system, messages, tools):
        tool = next(t for t in tools if t.name == 'query_table')
        result = tool.handler({'table_id': table.id, 'sql': 'DELETE FROM anything'})
        assert result['error'] == 'select_only'
        return llm.ChatResult('Invented count: 99', [])
    monkeypatch.setattr(chats['openai'], 'run', run)
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    answer = rag.answer(workspace, None, 'How many plots?')
    assert answer.uncertain and '99' not in answer.text


def test_legacy_spreadsheet_chunks_cannot_supply_partial_totals(client, tenant):
    from app.db import Chunk, Source, uid
    with Session.begin() as db:
        source = Source(id=uid(), tenant_id=tenant['tenant_id'], kind='csv', filename='old.csv',
                        status='ready', file_path='old.csv')
        db.add(source)
        db.flush()
        db.add(Chunk(source_id=source.id, tenant_id=tenant['tenant_id'], position=0,
                     content='2BHK price: 3500000', embedding=[1, 0, 0]))
    assert rag.retrieve(tenant['tenant_id'], '2BHK', [1, 0, 0]) == []
