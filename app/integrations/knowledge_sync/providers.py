"""Read selected Drive trees and explicit Notion pages; never follow content URLs."""
import re
import time
from pathlib import Path
from uuid import UUID

from .http import HTTP, SyncError

ID = re.compile(r'^[A-Za-z0-9_-]{10,128}$')
FOLDER = 'application/vnd.google-apps.folder'
EXPORTS = {'application/vnd.google-apps.document': ('docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    'application/vnd.google-apps.spreadsheet': ('xlsx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    'application/vnd.google-apps.presentation': ('pdf', 'application/pdf')}
FIELDS = 'id,name,mimeType,modifiedTime,version,trashed,capabilities(canDownload)'


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SyncError('invalid_response')
    return value


class Drive:
    def __init__(self, token):
        self.token, self.http = token, HTTP()

    def get(self, path, **kwargs):
        return self.http.request('drive', 'GET', path, token=self.token, **kwargs)

    def snapshot(self, selection, cursor):
        changed = set()
        if cursor:
            for _ in range(20):
                try:
                    data = self.get('changes', params={'pageToken': cursor, 'pageSize': 1000,
                        'includeRemoved': 'true', 'includeItemsFromAllDrives': 'true', 'supportsAllDrives': 'true',
                        'fields': 'nextPageToken,newStartPageToken,changes(fileId,removed)'})
                except SyncError as exc:
                    if exc.status != 410:
                        raise
                    cursor = None
                    break  # Expired feed tokens require a complete rescan.
                changed.update(c['fileId'] for c in data['changes'] if c.get('fileId'))
                if not data.get('nextPageToken'):
                    cursor = data['newStartPageToken']
                    break
                cursor = data['nextPageToken']
            else:
                raise SyncError('scan_limit')
        if not cursor:
            cursor = self.get('changes/startPageToken', params={'supportsAllDrives': 'true'})['startPageToken']
            changed = None
        pending, visited, items = [(identity(v), 0) for v in selection], set(), {}
        while pending:
            folder, depth = pending.pop()
            if folder in visited:
                continue
            if len(visited) >= 50 or depth > 5:
                raise SyncError('scan_limit')
            visited.add(folder)
            try:
                root = self.get('files/' + folder, params={'supportsAllDrives': 'true', 'fields': FIELDS})
            except SyncError as exc:
                if exc.code == 'missing':
                    continue
                raise
            if root.get('trashed'):
                continue
            if root['mimeType'] != FOLDER:
                raise SyncError('choose_folders')
            page = None
            for _ in range(30):
                data = self.get('files', params={'q': f"'{folder}' in parents and trashed = false", 'pageSize': 100,
                    'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
                    'fields': 'incompleteSearch,nextPageToken,files(' + FIELDS + ')',
                    **({'pageToken': page} if page else {})})
                if data.get('incompleteSearch'):
                    raise SyncError('incomplete_scan')
                for row in data['files']:
                    external = identity(row['id'])
                    if row['mimeType'] == FOLDER:
                        pending.append((external, depth + 1))
                        continue
                    extension = EXPORTS.get(row['mimeType'], (Path(row['name']).suffix.lstrip('.').lower(), None))[0]
                    if (row.get('trashed') or row['mimeType'].startswith('application/vnd.google-apps.')
                            and row['mimeType'] not in EXPORTS or extension not in (
                                'pdf', 'docx', 'txt', 'md', 'csv', 'xlsx', 'json')
                            or not row.get('capabilities', {}).get('canDownload', False)):
                        continue
                    items[external] = {'id': external, 'name': row['name'][:240] + '.' + extension
                        if row['mimeType'] in EXPORTS else row['name'][:255], 'format': extension,
                        'mime': row['mimeType'], 'version': str(row.get('version', '')) + ':' + row['modifiedTime'],
                        'url': 'https://drive.google.com/file/d/' + external + '/view'}
                    if len(items) > 200:
                        raise SyncError('scan_limit')
                page = data.get('nextPageToken')
                if not page:
                    break
            else:
                raise SyncError('scan_limit')
        return list(items.values()), cursor, changed

    def content(self, item):
        export = EXPORTS.get(item['mime'])
        return self.get('files/' + item['id'] + ('/export' if export else ''), binary=True,
                        params={'mimeType': export[1]} if export else {'alt': 'media', 'supportsAllDrives': 'true'})


class Notion:
    def __init__(self, token):
        self.token, self.http, self.last = token, HTTP(), 0

    def get(self, path, **kwargs):
        time.sleep(max(0, .35 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        return self.http.request('notion', 'GET', path, token=self.token, **kwargs)

    def snapshot(self, selection, cursor):
        items = []
        for value in selection:
            external = str(UUID(value))
            try:
                page = self.get('pages/' + external)
            except SyncError as exc:
                if exc.code == 'missing':
                    continue
                raise
            if page.get('archived') or page.get('in_trash'):
                continue
            title = next((''.join(t.get('plain_text', '') for t in p['title'])
                          for p in page['properties'].values() if p.get('type') == 'title'), external)
            items.append({'id': external, 'name': title[:250] + '.md', 'format': 'md',
                          'version': page['last_edited_time'], 'url': 'https://www.notion.so/' + UUID(external).hex})
        return items, None, None

    def content(self, item):
        data = self.get('pages/' + item['id'] + '/markdown', params={'markdown_version': 'v2'})
        if data.get('truncated', True) or data.get('unknown_block_ids') or data.get('warnings'):
            raise SyncError('incomplete_page')
        text = data['markdown']
        if not isinstance(text, str) or re.search(r'<unknown\b', text) or len(text.encode()) > 1_000_000:
            raise SyncError('document_limit')
        return text.encode()
