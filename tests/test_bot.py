from app import rag


def test_bot_preview_is_tenant_scoped_and_has_no_side_effects(client, tenant, monkeypatch):
    from app.db import Conversation, Message, Session
    from sqlalchemy import func, select

    calls = []
    def preview(t, conversation_id, question):
        calls.append((t.id, conversation_id, question))
        return rag.Answer(text='Preview answer', sources=[{'number': 1, 'filename': 'faq.txt',
                          'heading': None, 'page': None}], search_query=question)

    monkeypatch.setattr(rag, 'answer', preview)
    url = '/api/v1/bot/test'
    assert client.post(url, json={'message': 'hello'}).status_code == 401
    response = client.post(url, headers=tenant['headers'], json={'message': 'hello'})
    assert response.status_code == 200
    assert response.json()['answer'] == 'Preview answer'
    assert calls == [(tenant['tenant_id'], None, 'hello')]
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 0
        assert db.scalar(select(func.count()).select_from(Message)) == 0
    assert client.post(url, headers=tenant['headers'], json={'message': ''}).status_code == 422
