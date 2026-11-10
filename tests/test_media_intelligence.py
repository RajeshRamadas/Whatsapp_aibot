"""Real bounded audio decoding, fake provider interpretation and durable checkpoints."""
import io
import wave
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import llm, rag
from app.config import settings
from app.db import Message, Session, UsageEvent
from app.services import media
from app.services import media_intelligence as intelligence
from tests.test_media import no_session_io as no_session_io
from tests.test_media import payload
from tests.test_whatsapp import link_number, post


def voice(seconds=1):
    data = io.BytesIO()
    with wave.open(data, 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * (16000 * seconds))
    return data.getvalue()


def test_decodes_and_enforces_actual_five_minute_limit():
    wav, duration = intelligence.audio(voice())
    assert duration == 1 and wav.startswith(b'RIFF')
    assert intelligence.audio(voice(300))[1] == 300
    with pytest.raises(intelligence.AudioTooLong):
        intelligence.audio(voice(301))
    with pytest.raises(ValueError):
        intelligence.audio(b'not an audio file')


def test_transcript_answers_and_is_reused_after_answer_failure(client, tenant, sent, monkeypatch, no_session_io):
    link_number(client, tenant)
    monkeypatch.setattr(settings, 'transcription_provider', 'openai')
    intelligence.limiter.clear()
    monkeypatch.setattr(media, 'download', lambda *args: (voice(), 'audio/wav'))
    calls = []
    def transcribe(data):
        assert no_session_io()
        calls.append(data)
        return 'आप कब खुलते हैं?', 'hi'
    monkeypatch.setattr(intelligence, 'transcribe', transcribe)
    answer = rag.answer
    failures = [True]
    def flaky(*args, **kwargs):
        if failures:
            failures.pop()
            raise RuntimeError('fixture answer failure')
        return answer(*args, **kwargs)
    monkeypatch.setattr(rag, 'answer', flaky)
    assert post(client, payload('audio')).status_code == 503
    assert post(client, payload('audio')).status_code == 200
    assert len(calls) == 1 and 'आप कब खुलते हैं?' in sent[0][2]
    with Session() as db:
        row = db.scalar(select(Message).where(Message.role == 'user'))
        assert row.content == row.attachments[0]['transcript'] == 'आप कब खुलते हैं?'
        usage = db.scalar(select(UsageEvent).where(UsageEvent.kind == 'transcription'))
        assert usage.audio_seconds == Decimal('1.000') and usage.cost_usd is None


@pytest.mark.parametrize('mode', ['disabled', 'too_long', 'unavailable', 'not_entitled'])
def test_voice_fallback_preserves_audio_for_agents(client, tenant, sent, monkeypatch, mode):
    link_number(client, tenant)
    monkeypatch.setattr(settings, 'transcription_provider', 'disabled' if mode == 'disabled' else 'openai')
    intelligence.limiter.clear()
    if mode == 'not_entitled':
        monkeypatch.setattr(intelligence, 'entitled', lambda *args: False)
    monkeypatch.setattr(media, 'download', lambda *args: (voice(), 'audio/wav'))
    def reject(data):
        if mode == 'too_long':
            raise intelligence.AudioTooLong('fixture')
        raise RuntimeError('fixture provider timeout')
    monkeypatch.setattr(intelligence, 'audio' if mode == 'too_long' else 'transcribe', reject)
    assert post(client, payload('audio')).status_code == 200
    with Session() as db:
        row = db.scalar(select(Message).where(Message.role == 'user'))
        assert row.attachments[0]['interpretation'] == ('disabled' if mode == 'not_entitled' else mode)
        assert media.storage().get(row.attachments[0]['key']).startswith(b'RIFF')
    assert client.get('/api/v1/conversations', headers=tenant['headers']).json()[0]['handoff']


def test_image_is_interpreted_by_workspace_chat_provider(client, tenant, sent, chats, monkeypatch):
    link_number(client, tenant)
    monkeypatch.setattr(settings, 'media_vision_enabled', True)
    intelligence.limiter.clear()
    monkeypatch.setattr(media, 'download', lambda *args: (b'\x89PNG\r\n\x1a\nfixture', 'image/png'))
    calls = []
    def describe(model, data, mime):
        calls.append((model, mime))
        return 'A customer asks about opening hours.'
    monkeypatch.setattr(chats['openai'], 'image', describe, raising=False)
    assert post(client, payload('image')).status_code == 200
    assert calls and 'opening hours' in sent[0][2]
    with Session() as db:
        assert db.scalar(select(Message).where(Message.role == 'user')).attachments[0]['description']


def test_openai_adapter_uses_automatic_language_detection(monkeypatch):
    import openai
    calls = []
    def transcribe(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='  नमस्ते  ', language=None)
    monkeypatch.setattr(settings, 'openai_api_key', 'fixture')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: SimpleNamespace(audio=SimpleNamespace(
        transcriptions=SimpleNamespace(create=transcribe))))
    assert intelligence.transcribe(b'fixture') == ('नमस्ते', None)
    assert 'language' not in calls[0] and calls[0]['file'][0] == 'voice.wav'


@pytest.mark.parametrize('adapter', [llm.OpenAIChat, llm.AnthropicChat])
def test_image_adapters_send_actual_bytes(monkeypatch, adapter):
    instance = object.__new__(adapter)
    calls = []
    monkeypatch.setattr(instance, 'complete', lambda model, system, messages: calls.append(messages) or 'visible text')
    assert instance.image('fixture-model', b'image', 'image/png') == 'visible text'
    assert 'aW1hZ2U=' in str(calls[0])


def test_interrupted_provider_call_is_not_repeated(client, tenant, monkeypatch):
    from app import whatsapp
    link_number(client, tenant)
    intelligence.limiter.clear()
    monkeypatch.setattr(settings, 'transcription_provider', 'openai')
    monkeypatch.setattr(media, 'download', lambda *args: (voice(), 'audio/wav'))
    parts = whatsapp.parse_messages(payload('audio'))
    whatsapp.accept(parts[0])
    calls = []
    def interrupted(data):
        calls.append(data)
        raise SystemExit('fixture worker interruption')
    monkeypatch.setattr(intelligence, 'transcribe', interrupted)
    with pytest.raises(SystemExit):
        intelligence.interpret(tenant['tenant_id'], parts, media.prepare(tenant['tenant_id'], parts))
    assert intelligence.interpret(tenant['tenant_id'], parts, media.prepare(tenant['tenant_id'], parts)) == ''
    assert len(calls) == 1


def test_evaluation_word_error_counts_without_provider_calls():
    from scripts.evaluate_speech import errors
    assert errors('नमस्ते, दुनिया', 'नमस्ते दुनिया') == (0, 2)
    assert errors('business hours today', 'business today') == (1, 3)
