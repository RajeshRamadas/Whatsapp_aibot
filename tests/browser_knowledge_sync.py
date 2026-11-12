"""Only imported by the isolated browser application; production routes never load this file."""
from fastapi import Depends
from pydantic import BaseModel

from app.api.principal import Principal, require_role
from app.config import settings
from app.integrations.knowledge_sync.http import HTTP
from app.services import knowledge_connections, knowledge_sync

settings.knowledge_google_client_id = 'browser-drive-client'
settings.knowledge_google_client_secret = 'browser-drive-secret'
settings.knowledge_google_redirect_uri = 'http://localhost:3000/api/backend/knowledge-sync/callback'
state = {'removed': False, 'version': 1}


def fake(self, provider, method, path, **kwargs):
    # Parallel browser requests may legitimately hold other pool connections.
    # The serial provider tests check that sync itself releases its DB session.
    if provider == 'oauth':
        return {'access_token': 'browser-drive-access', 'refresh_token': 'browser-drive-refresh',
                'expires_in': 3600, 'scope': knowledge_connections.SCOPE}
    assert method == 'GET'
    if provider == 'notion':
        if path.endswith('/markdown'):
            return {'markdown': '# Refunds\n\nRefunds accepted within 30 days.',
                    'truncated': False, 'unknown_block_ids': []}
        return {'archived': state['removed'], 'properties': {'title': {'type': 'title',
            'title': [{'plain_text': 'Refund policy'}]}}, 'last_edited_time': str(state['version'])}
    if path == 'changes/startPageToken':
        return {'startPageToken': 'browser-cursor'}
    if path == 'changes':
        return {'changes': [], 'newStartPageToken': 'browser-cursor'}
    if path == 'files/rootfolder123':
        return {'mimeType': 'application/vnd.google-apps.folder'}
    if path == 'files':
        return {'files': [] if state['removed'] else [{'id': 'drivepolicy123', 'name': 'Drive policy.md',
            'mimeType': 'text/markdown', 'version': str(state['version']), 'modifiedTime': '2026-11-12',
            'capabilities': {'canDownload': True}}]}
    assert path == 'files/drivepolicy123'
    return b'# Refunds\n\nRefunds accepted within 30 days.'


original = knowledge_connections.change


def change(tenant_id, identity, **kwargs):
    original(tenant_id, identity, **kwargs)
    if kwargs.get('disconnect'):
        return
    knowledge_sync.process_one()
    return next(c for c in knowledge_connections.listing(tenant_id)['connections'] if c['id'] == identity)


HTTP.request = fake
knowledge_connections.change = change


class Scenario(BaseModel):
    removed: bool


def install(app):
    @app.post('/api/v1/_test/knowledge-sync', include_in_schema=False)
    def scenario(body: Scenario, p: Principal = Depends(require_role('admin'))):
        # Test data only, bound to the synthetic workspace.
        assert p.tenant.public_key == 'pub_knowledge_sync_demo'
        state.update(removed=body.removed, version=state['version'] + 1)
        return {'ok': True}
