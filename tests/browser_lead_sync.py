"""Browser-only connector fake; native protocols are covered by test_crm_delivery/test_sheet_delivery."""
import asyncio

from app.config import settings
from app.integrations.lead_sync.http import HTTP, SyncError
from app.main import app
from app.services import lead_sync_worker

settings.lead_sync_google_client_id = 'browser-lead-client'
settings.lead_sync_google_client_secret = 'browser-lead-secret'
settings.lead_sync_google_redirect_uri = 'http://localhost:3000/api/backend/lead-sync/google/callback'
_delivered, _fail = {}, set()
_original = HTTP.request


def browser_token(self, method, path='', **kwargs):
    if self.root == 'https://oauth2.googleapis.com/' and path == 'token':
        return {'access_token': 'lead-access', 'refresh_token': 'lead-refresh', 'expires_in': 3600}
    return _original(self, method, path, **kwargs)


HTTP.request = browser_token


class BrowserConnector:
    def __init__(self, tenant_id, connection):
        self.connection = connection

    def inventory(self, file_id=None, previous=None):
        if self.connection.provider == 'sheets':
            return {'files': [{'id': 'browser_sheet', 'name': 'Lead export', 'tabs': [{'id': 0, 'title': 'Leads'}]}]}
        names = (['firstname', 'phone', 'email'] if self.connection.provider == 'hubspot'
                 else ['Last_Name', 'Phone', 'Email'])
        return {'fields': [{'name': n, 'label': n, 'required': n == 'Last_Name'} for n in names]}

    def create_sheet(self, job, checkpoint, previous):
        checkpoint(submitted=True, remote_id='browser_sheet')
        return self.inventory()

    def deliver(self, job, config, checkpoint):
        checkpoint()
        if job.connection_id in _fail:
            _fail.remove(job.connection_id)
            raise SyncError('record_rejected')
        _delivered.setdefault(job.connection_id, set()).add(job.lead_version)


lead_sync_worker.client = BrowserConnector


@app.post('/api/v1/_test/lead-sync/{identity}/fail', include_in_schema=False)
def fail_next(identity: str):
    _fail.add(identity)
    return {'ok': True}


@app.get('/api/v1/_test/lead-sync/{identity}', include_in_schema=False)
def delivered(identity: str):
    return {'versions': sorted(_delivered.get(identity, []))}


@app.middleware('http')
async def lead_sync_fixture(request, call_next):
    response = await call_next(request)
    if response.status_code < 400 and (request.method != 'GET' and ('/lead-sync' in request.url.path
            or '/leads/' in request.url.path) or request.url.path.endswith('/lead-sync/google/callback')):
        for _ in range(15):
            if not await asyncio.to_thread(lead_sync_worker.process_one):
                break
    return response
