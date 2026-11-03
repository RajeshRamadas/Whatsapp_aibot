const OFFLINE_CACHE = 'inbox-offline-v1';
const SNAPSHOT = '/dashboard/inbox-offline-snapshot';

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

function escapeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

self.addEventListener('message', event => {
  if (!event.source || new URL(event.source.url).origin !== self.location.origin) return;
  if (event.data?.type === 'CLEAR_INBOX') {
    event.waitUntil(caches.delete(OFFLINE_CACHE));
  } else if (event.data?.type === 'INBOX_SNAPSHOT') {
    const { workspace, conversations, messages } = event.data;
    const list = (conversations || []).map(row =>
      '<li>' + escapeText(row.user) + ' · ' + escapeText(row.status) + '</li>').join('');
    const transcript = (messages || []).map(row =>
      '<li><strong>' + escapeText(row.kind === 'note' ? 'Internal note' : row.role) +
      '</strong><p>' + escapeText(row.content) + '</p></li>').join('');
    const html = '<!doctype html><html lang="en"><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline inbox</title>' +
      '<style>body{font:16px system-ui;max-width:50rem;margin:auto;padding:1rem}' +
      'li{margin:1rem 0;overflow-wrap:anywhere}p{white-space:pre-wrap}</style>' +
      '<h1>Offline inbox</h1><p>' + escapeText(workspace) +
      '</p><p>Read-only copy of your last-loaded inbox. Reconnect to reply or refresh.</p>' +
      '<a href="/dashboard/inbox">Reconnect</a><h2>Conversations</h2><ul>' + list +
      '</ul><h2>Last open conversation</h2><ol>' + transcript + '</ol></html>';
    event.waitUntil(caches.open(OFFLINE_CACHE).then(cache => cache.put(SNAPSHOT,
      new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8',
        'X-Snapshot-Time': String(Date.now()) } }))));
  }
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.mode !== 'navigate' || url.origin !== self.location.origin ||
      url.pathname !== '/dashboard/inbox') return;
  event.respondWith(fetch(event.request).catch(async () => {
    const saved = await caches.match(SNAPSHOT);
    if (saved && Date.now() - Number(saved.headers.get('X-Snapshot-Time')) < 15 * 60 * 1000) return saved;
    await caches.delete(OFFLINE_CACHE);
    return new Response('Reconnect to load your inbox.', {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' }, status: 503,
    });
  }));
});

self.addEventListener('push', event => {
  let workspace = '';
  try { workspace = event.data?.json()?.workspace_id || ''; } catch { /* Use the current workspace. */ }
  event.waitUntil(self.registration.showNotification('Inbox needs attention', {
    body: 'Open the inbox to review the latest activity.',
    icon: '/inbox-icon/192', tag: 'inbox-attention-' + workspace,
    data: { workspace },
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = new URL('/dashboard/inbox/open', self.location.origin);
    const workspace = event.notification.data?.workspace;
    if (workspace) url.searchParams.set('workspace', workspace);
    await self.clients.openWindow(url.href);
  })());
});
