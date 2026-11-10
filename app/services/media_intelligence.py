"""Optional speech/vision interpretation; provider choice remains deployment explicit."""
import io
import subprocess
import wave
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .. import llm
from ..config import settings
from ..db import InboundEvent, Session, Tenant, UsageEvent, now
from ..observability import integration
from ..rate_limits import RateLimiter
from .billing import entitled
from .storage import storage
from .usage import meter

limiter = RateLimiter(20, window_seconds=60, namespace='media-interpretation')


class AudioTooLong(ValueError):
    pass


def audio(data):
    """Decode at most 301 seconds locally; actual PCM length enforces the five-minute limit."""
    try:
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-max_alloc', '67108864', '-threads', '1',
            '-protocol_whitelist', 'pipe',
            '-i', 'pipe:0', '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-t', '301',
            '-f', 's16le', 'pipe:1'], input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('Audio could not be decoded') from None
    seconds = Decimal(len(result.stdout)) / 32000
    if seconds > 300:
        raise AudioTooLong('Audio is longer than five minutes')
    if not seconds:
        raise ValueError('Audio is empty')
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        writer.writeframes(result.stdout)
    return output.getvalue(), seconds


def transcribe(data):
    from openai import OpenAI
    client = OpenAI(api_key=llm._require(settings.openai_api_key, 'OPENAI_API_KEY'), timeout=45, max_retries=0)
    with integration('openai'):
        result = client.audio.transcriptions.create(model=settings.transcription_model,
            file=('voice.wav', data, 'audio/wav'), response_format='json')
    return result.text.strip(), getattr(result, 'language', None)


def checkpoint(tenant_id, parts, item):
    with Session.begin() as db:
        for part in parts:
            event = db.scalar(select(InboundEvent).where(InboundEvent.tenant_id == tenant_id,
                              InboundEvent.channel_message_id == part['id']))
            if event and any(row['id'] == item['id'] for row in event.payload.get('attachments', [])):
                event.payload = {**event.payload, 'attachments': [item if row['id'] == item['id'] else row
                                 for row in event.payload['attachments']]}


def interpret(tenant_id, parts, attachments, *, vision=True):
    """Checkpoint interpretation separately from answering so answer retries reuse transcripts."""
    with Session() as db:
        tenant = db.get(Tenant, tenant_id)
        allowed = bool(tenant and not tenant.suspended and entitled(db, tenant, now()))
    for item in attachments:
        kind = item['kind']
        if kind not in ('audio', 'image') or item['status'] != 'ready' or item.get('interpretation'):
            continue
        if (not allowed or (kind == 'audio' and settings.transcription_provider == 'disabled') or
                (kind == 'image' and (not vision or not settings.media_vision_enabled))):
            item['interpretation'] = 'disabled'
        elif not limiter.allow(tenant_id):
            item['interpretation'] = 'limited'
        else:
            item['interpretation'] = 'processing'
            checkpoint(tenant_id, parts, item)
            data = storage().get(item['key'])
            duration = None
            try:
                if kind == 'audio':
                    data, duration = audio(data)
                    text, language = transcribe(data)
                    item.update(transcript=text, language=language, audio_seconds=str(duration))
                else:
                    name, model = llm.resolve_chat(tenant.chat_provider, tenant.chat_model)
                    provider = llm.chat_provider(name)
                    with meter(tenant_id):
                        text = provider.image(model, data, item['mime_type'])
                    item['description'] = text.strip()
                item['interpretation'] = 'ready' if text.strip() else 'unreadable'
            except AudioTooLong:
                item['interpretation'] = 'too_long'
            except llm.ProviderNotConfigured:
                duration = None
                item['interpretation'] = 'unavailable'
            except ValueError:
                item['interpretation'] = 'unavailable'
            except Exception:
                # A provider timeout may already be billable: do not silently transcribe it again.
                item['interpretation'] = 'unavailable'
            if duration is not None:
                with Session.begin() as db:
                    db.execute(insert(UsageEvent).values(tenant_id=tenant_id, kind='transcription',
                        provider='openai', model=settings.transcription_model, audio_seconds=duration,
                        request_key='audio:' + item['id']).on_conflict_do_nothing(
                            constraint='uq_usage_request'))
        checkpoint(tenant_id, parts, item)
    for item in attachments:
        if item.get('transcript'):
            for part in parts:
                if item['id'] == str(uuid5(NAMESPACE_URL, f'{tenant_id}:{part["id"]}')):
                    part['text'] = item['transcript']
    return '\n'.join(item.get('description') or '' for item in attachments).strip()
