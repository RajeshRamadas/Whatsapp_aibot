"""Facebook Login for Page and linked professional Instagram account discovery."""
from urllib.parse import urlencode, urlparse

from ...config import settings
from ...services.meta_signup import MetaSignupError

SCOPES = {
    'messenger': 'pages_show_list,pages_read_engagement,pages_manage_metadata,pages_messaging',
    'instagram': ('pages_show_list,pages_read_engagement,pages_manage_metadata,'
                  'instagram_basic,instagram_manage_messages'),
}


def configured():
    return bool(settings.whatsapp_app_id and settings.whatsapp_app_secret and settings.whatsapp_verify_token
                and settings.master_key and settings.meta_dm_oauth_redirect_uri
                and valid_redirect(settings.meta_dm_oauth_redirect_uri))


def valid_redirect(value):
    try:
        url = urlparse(value)
    except ValueError:
        return False
    return bool(url.hostname and not url.username and not url.password and not url.query and not url.fragment
                and url.path == '/api/backend/meta-dm/oauth/callback'
                and (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1'))))


def authorization_url(kind, state):
    params = {'client_id': settings.whatsapp_app_id, 'redirect_uri': settings.meta_dm_oauth_redirect_uri,
              'response_type': 'code', 'state': state}
    if settings.meta_dm_oauth_config_id:
        params['config_id'] = settings.meta_dm_oauth_config_id
    else:
        params['scope'] = SCOPES[kind]
    return f'https://www.facebook.com/{settings.whatsapp_graph_version}/dialog/oauth?' + urlencode(params)


def discover(graph, code, redirect_uri, kind):
    # Codes and bearer credentials never cross the browser or logs.
    args = {'client_id': settings.whatsapp_app_id, 'client_secret': settings.whatsapp_app_secret}
    result = graph.request('oauth/access_token', params={**args, 'code': code, 'redirect_uri': redirect_uri})
    token = result.get('access_token')
    if not isinstance(token, str) or not 1 <= len(token) <= 8192:
        raise MetaSignupError('Invalid token exchange')
    result = graph.request('oauth/access_token', params={**args, 'grant_type': 'fb_exchange_token',
                                                        'fb_exchange_token': token})
    token = result.get('access_token')
    if not isinstance(token, str) or not 1 <= len(token) <= 8192:
        raise MetaSignupError('Invalid long-lived token exchange')
    fields = 'id,name,access_token' + (',instagram_business_account{id,name,ig_id}' if kind == 'instagram' else '')
    params, accounts = {'fields': fields, 'limit': 100}, {}
    for _ in range(10):
        result = graph.request('me/accounts', params=params, token=token)
        rows = result.get('data')
        if not isinstance(rows, list):
            raise MetaSignupError('Invalid account catalog')
        for page in rows:
            if not isinstance(page, dict):
                raise MetaSignupError('Invalid account catalog')
            asset = page if kind == 'messenger' else page.get('instagram_business_account')
            credential = page.get('access_token')
            if not isinstance(asset, dict) or not isinstance(credential, str) or not 1 <= len(credential) <= 8192:
                continue
            identity = str(asset.get('id') or '')
            if not identity.isascii() or not identity.isdigit() or not 1 <= len(identity) <= 32:
                raise MetaSignupError('Invalid account identity')
            accounts[identity] = {'external_id': identity, 'name': str(asset.get('name') or identity)[:200],
                                  'kind': kind, 'token': credential}
            if len(accounts) > 100:
                raise MetaSignupError('Too many authorized accounts')
        paging = result.get('paging') or {}
        if not paging.get('next'):
            return list(accounts.values())
        after = (paging.get('cursors') or {}).get('after')
        if not isinstance(after, str) or not 1 <= len(after) <= 4096:
            break
        params['after'] = after
    raise MetaSignupError('Account catalog exceeds bounds')
