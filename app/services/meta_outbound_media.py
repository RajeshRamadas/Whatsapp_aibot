"""Private staging and single-request Meta media delivery using verified Page tokens."""
import hashlib
import json

import httpx

from ..config import settings
from ..observability import integration
from . import audio_validation, media
from .crypto import decrypt
from .meta_signup import MetaSignupError

SUFFIXES = {'image/jpeg': '.jpg', 'image/png': '.png', 'application/pdf': '.pdf',
            'audio/aac': '.aac', 'audio/mp4': '.m4a', 'audio/wav': '.wav'}
MIMES = set(SUFFIXES)


def stage(tenant_id, item, data):
    if (item.get('mime_type') not in MIMES or item.get('key') != media.key_for(tenant_id, item['id'])
            or hashlib.sha256(data).hexdigest() != item.get('sha256')):
        raise ValueError('Invalid Meta attachment')
    media.validate(data, item['mime_type'], item['kind'])
    if item['kind'] == 'audio':
        audio_validation.validate(data, item['mime_type'])
    media.storage().put(item['key'], data)
    item['status'] = 'ready'


@integration('meta_signup')
def send(channel, recipient, item, data):
    """Upload and send together: private files never need a publicly reachable URL."""
    token = decrypt(channel.tenant_id, channel.credentials_enc).decode()
    suffix = SUFFIXES[item['mime_type']]
    form = {'recipient': json.dumps({'id': recipient}), 'messaging_type': 'RESPONSE',
            'message': json.dumps({'attachment': {
                'type': 'file' if item['kind'] == 'document' else item['kind'], 'payload': {}}})}
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            # Page tokens resolve /me to the owning Page for both supported channel kinds.
            with client.stream('POST', f'https://graph.facebook.com/{settings.whatsapp_graph_version}/me/messages',
                headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                         'Accept-Encoding': 'identity'}, data=form,
                files={'filedata': ('attachment' + suffix, data, item['mime_type'])}) as response:
                response.raise_for_status()
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError('Invalid Meta response encoding')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError('Invalid Meta response')
                result = json.loads(raw)
        if (not isinstance(result, dict) or result.get('recipient_id') != recipient or 'error' in result
                or not isinstance(result.get('message_id'), str) or not 1 <= len(result['message_id']) <= 255):
            raise ValueError('Invalid Meta confirmation')
        return result['message_id']
    except (httpx.HTTPError, ValueError, UnicodeError, OSError):
        raise MetaSignupError('Meta attachment delivery could not be confirmed') from None
