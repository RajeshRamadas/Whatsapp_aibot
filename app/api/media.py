"""Authenticated attachment access and durable, idempotent agent media replies."""
import hashlib
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..config import settings
from ..db import Message, Session, now, uid
from ..services import media
from ..services.events import publish_sync
from ..services.storage import LocalStorage, storage
from .inbox import can_reply, conversation
from .principal import Principal, require_role

router = APIRouter()


class AttachmentView(BaseModel):
    id: str
    kind: str
    status: str
    filename: str | None = None
    mime_type: str | None = None
    size: int = 0
    details: dict | list | None = None
    interpretation: str | None = None
    transcript: str | None = None
    description: str | None = None
    language: str | None = None
    audio_seconds: str | None = None


class QueuedMedia(BaseModel):
    status: str
    message_id: int


def public(attachments):
    return [{key: value for key, value in item.items() if key not in ('key', 'sha256')} for item in attachments]


@router.get('/api/v1/conversations/{conversation_id}/media/{attachment_id}')
def download(conversation_id: UUID, attachment_id: UUID,
             principal: Principal = Depends(require_role('viewer'))):
    with Session() as db:
        conv = conversation(db, principal.tenant.id, str(conversation_id))
        rows = db.scalars(select(Message).where(Message.conversation_id == conv.id,
            Message.created_at >= now() - timedelta(days=settings.media_retention_days),
            Message.attachments.op('@>')([{'id': str(attachment_id)}]))).all()
        item = next((item for row in rows for item in row.attachments if item['id'] == str(attachment_id)), None)
    if not item or item.get('status') != 'ready' or item.get('key') != media.key_for(principal.tenant.id, item['id']):
        raise HTTPException(404, 'Attachment is unavailable or expired')
    store = storage()
    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
    if isinstance(store, LocalStorage):
        path = store._path(item['key'])
        if not path.is_file():
            raise HTTPException(404, 'Attachment is unavailable or expired')
        return FileResponse(path, media_type=item['mime_type'], filename=item['filename'], headers=headers,
                            content_disposition_type='inline' if item['kind'] in ('image', 'audio') else 'attachment')
    return RedirectResponse(store.presign_get(item['key'], content_type=item['mime_type'], filename=item['filename'],
                            inline=item['kind'] in ('image', 'audio')), status_code=302, headers=headers)


@router.post('/api/v1/inbox/conversations/{conversation_id}/media', response_model=QueuedMedia, status_code=202)
async def reply(conversation_id: UUID, file: UploadFile = File(...), caption: str = Form('', max_length=1024),
                idempotency_key: str = Header(..., min_length=1, max_length=128),
                principal: Principal = Depends(require_role('agent'))):
    mime = (file.content_type or '').split(';')[0].lower()
    kind = 'image' if mime.startswith('image/') else 'document'
    data = await file.read(settings.media_max_bytes + 1)
    if len(data) > settings.media_max_bytes:
        raise HTTPException(413, 'Attachment exceeds the size limit')
    try:
        media.validate(data, mime, kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    digest = hashlib.sha256(data).hexdigest()
    with Session.begin() as db:
        conv = conversation(db, principal.tenant.id, str(conversation_id))
        db.refresh(conv, with_for_update=True)
        author = principal.user.id if principal.user else None
        previous = db.scalar(select(Message).where(Message.conversation_id == conv.id,
                             Message.reply_key == idempotency_key))
        if previous:
            if (previous.content != caption or previous.author_user_id != author or not previous.attachments
                    or previous.attachments[0].get('sha256') != digest
                    or previous.attachments[0].get('filename') != media.filename(file.filename)
                    or previous.attachments[0].get('mime_type') != mime):
                raise HTTPException(409, 'Idempotency key already used for another reply')
            return {'status': previous.delivery_status, 'message_id': previous.id}
        if conv.channel != 'whatsapp' or conv.status == 'closed' or not can_reply(conv):
            raise HTTPException(409, 'An open WhatsApp conversation within the 24-hour window is required')
        from ..services.channels import whatsapp_channel
        channel = whatsapp_channel(db, principal.tenant.id)
        if not settings.whatsapp_enabled or not (settings.whatsapp_access_token or
                                                  (channel and channel.credentials_enc)):
            raise HTTPException(503, 'WhatsApp is not configured')
        identity = uid()
        message = Message(conversation_id=conv.id, role='agent', direction='outbound', kind=kind,
            author_user_id=author, content=caption, delivery_status='queued', reply_key=idempotency_key,
            upload_data=data, attachments=[{'id': identity, 'kind': kind, 'filename': media.filename(file.filename),
                'mime_type': mime, 'size': len(data), 'sha256': digest, 'status': 'queued',
                'key': media.key_for(principal.tenant.id, identity)}])
        db.add(message)
        conv.handoff, conv.status, conv.last_message_at = True, 'agent', now()
        db.flush()
        result = {'status': 'queued', 'message_id': message.id}
    publish_sync(principal.tenant.id, 'message.created', {'conversation_id': str(conversation_id)})
    publish_sync(principal.tenant.id, 'conversation.updated', {'conversation_id': str(conversation_id)})
    return result
