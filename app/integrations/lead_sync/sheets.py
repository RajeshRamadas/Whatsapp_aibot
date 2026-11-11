"""App-accessible spreadsheet discovery and literal, append-only lead rows."""
import re
from urllib.parse import quote

from ...config import settings
from .http import HTTP, SyncError

HEADERS = ('event_id', 'updated_at', 'conversation_url', 'name', 'phone', 'email', 'need', 'budget',
           'location', 'preferred_time', 'status', 'notes')


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', value):
        raise SyncError('invalid_response')
    return value


class Sheets:
    def __init__(self, tenant_id, token):
        headers = {'Authorization': 'Bearer ' + token}
        self.drive = HTTP(tenant_id, 'sheets', 'https://www.googleapis.com/drive/v3/', headers)
        self.sheets = HTTP(tenant_id, 'sheets',
                           'https://sheets.googleapis.com/' + settings.lead_sync_sheets_api_version + '/', headers)

    def inventory(self, file_id=None, previous=None):
        files, page = [], None
        for _ in range(5):
            data = self.drive.request('GET', 'files', params={
                'q': "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false", 'pageSize': 100,
                'fields': 'nextPageToken,files(id,name)', **({'pageToken': page} if page else {})})
            for f in data.get('files', []):
                old = next((v for v in (previous or {}).get('files', []) if v['id'] == f['id']), {})
                files.append({'id': identity(f['id']), 'name': str(f.get('name', ''))[:200],
                              'tabs': old.get('tabs', [])})
            page = data.get('nextPageToken')
            if not page:
                break
        else:
            raise SyncError('too_many_spreadsheets')
        if file_id:
            file = next((f for f in files if f['id'] == file_id), None)
            if not file:
                raise SyncError('spreadsheet_not_accessible')
            file['tabs'] = self.tabs(file_id)
        return {'files': files}

    def tabs(self, file_id):
        data = self.sheets.request('GET', 'spreadsheets/' + identity(file_id),
                                   params={'fields': 'sheets.properties(sheetId,title)'})
        rows = []
        for sheet in data.get('sheets', []):
            p = sheet['properties']
            if type(p.get('sheetId')) is not int or not 0 <= p['sheetId'] <= 2147483647:
                raise SyncError('invalid_response')
            rows.append({'id': p['sheetId'], 'title': str(p['title'])[:200]})
        if len(rows) > 500:
            raise SyncError('too_many_tabs')
        return rows

    def create_sheet(self, job, checkpoint, previous):
        data = self.drive.request('GET', 'files', params={
            'q': "trashed=false and appProperties has {key='lead_sync_job' and value='" + job.id + "'}",
            'fields': 'files(id,name)', 'pageSize': 2})
        files = data.get('files', [])
        if len(files) > 1:
            raise SyncError('spreadsheet_creation_ambiguous')
        if not files:
            if job.submitted:
                raise SyncError('creation_unconfirmed', retry=True)
            checkpoint(submitted=True)
            file = self.drive.request('POST', 'files', params={'fields': 'id,name'}, payload={
                'name': job.payload['title'], 'mimeType': 'application/vnd.google-apps.spreadsheet',
                'appProperties': {'lead_sync_job': job.id}})
        else:
            file = files[0]
        file_id = identity(file['id'])
        checkpoint(remote_id=file_id)
        if not self.tabs(file_id):
            checkpoint()
            self.sheets.request('POST', 'spreadsheets/' + file_id + ':batchUpdate', payload={
                'requests': [{'addSheet': {'properties': {'sheetId': 0, 'title': 'Leads'}}}]})
        return self.inventory(file_id, previous)

    def deliver(self, job, config, checkpoint):
        file_id = identity(config['spreadsheet_id'])
        tab = next((t for t in self.tabs(file_id) if t['id'] == config['tab_id']), None)
        if not tab:
            raise SyncError('tab_not_found')
        prefix = "'" + tab['title'].replace("'", "''") + "'!"
        data = self.sheets.request('GET', 'spreadsheets/' + file_id + '/values:batchGet', params={
            'ranges': [prefix + 'A1:L1', prefix + 'A2:A50001'], 'valueRenderOption': 'UNFORMATTED_VALUE'})
        ranges = data.get('valueRanges', [])
        if len(ranges) != 2:
            raise SyncError('invalid_response')
        first, rows = ranges[0].get('values', []), ranges[1].get('values', [])
        if any(r and r[0] == job.id for r in rows):
            return
        if first and first != [list(HEADERS)] or rows and not first:
            raise SyncError('export_header_mismatch')
        if len(rows) >= 50000:
            raise SyncError('export_tab_full')
        if job.submitted:
            raise SyncError('append_unconfirmed', retry=True)
        values = [str(job.payload.get(key, '')) for key in HEADERS]
        values[0] = job.id
        output = ([] if first else [list(HEADERS)]) + [values]
        checkpoint(submitted=True)
        self.sheets.request('POST', 'spreadsheets/' + quote(file_id, safe='') + ':batchUpdate', payload={
            'requests': [{'appendCells': {'sheetId': config['tab_id'], 'fields': 'userEnteredValue',
                'rows': [{'values': [{'userEnteredValue': {'stringValue': v}} for v in row]} for row in output]}}]})
