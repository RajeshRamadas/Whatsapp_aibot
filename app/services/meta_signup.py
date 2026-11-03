"""Meta Graph operations used by Embedded Signup."""
import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import settings


class MetaSignupError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class MetaSignup:
    opener = build_opener(NoRedirects())

    def request(self, path, *, params=None, token=None, payload=None):
        url = f'https://graph.facebook.com/{settings.whatsapp_graph_version}/{path}'
        if params:
            url += '?' + urlencode(params)
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=12) as response:
                result = json.load(response)
        except (URLError, OSError, ValueError):
            # OAuth URLs contain secrets. Never surface the URL or Graph response body.
            raise MetaSignupError('Meta could not complete the request') from None
        if not isinstance(result, dict) or 'error' in result:
            raise MetaSignupError('Meta rejected the request')
        return result

    def exchange(self, code):
        response = self.request('oauth/access_token', params={
            'client_id': settings.whatsapp_app_id, 'client_secret': settings.whatsapp_app_secret, 'code': code,
        })
        token = response.get('access_token')
        if not isinstance(token, str) or not token:
            raise MetaSignupError('Meta did not return an access token')
        return token

    def verify_phone(self, waba_id, phone_id, token):
        params = {'fields': 'id,verified_name', 'limit': 100}
        for _ in range(10):
            result = self.request(f'{waba_id}/phone_numbers', params=params, token=token)
            for phone in result.get('data', []):
                if phone.get('id') == phone_id:
                    return str(phone.get('verified_name') or '')[:200] or None
            paging = result.get('paging') or {}
            after = (paging.get('cursors') or {}).get('after')
            if not paging.get('next') or not after:
                break
            params['after'] = after
        raise MetaSignupError('The phone does not belong to the selected WhatsApp Business Account')

    def subscribe(self, waba_id, token):
        if self.request(f'{waba_id}/subscribed_apps', token=token, payload={}).get('success') is not True:
            raise MetaSignupError('Meta did not confirm webhook subscription')

    def register(self, phone_id, pin, token):
        body = {'messaging_product': 'whatsapp', 'pin': pin}
        if self.request(f'{phone_id}/register', token=token, payload=body).get('success') is not True:
            raise MetaSignupError('Meta did not confirm phone registration')

    def send_test(self, phone_id, recipient, token):
        result = self.request(f'{phone_id}/messages', token=token, payload={
            'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': recipient,
            'type': 'template', 'template': {'name': 'hello_world', 'language': {'code': 'en_US'}},
        })
        if not result.get('messages'):
            raise MetaSignupError('Meta did not accept the test message')
