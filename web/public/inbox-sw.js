importScripts('/inbox-messages.js');
const locale = new URL(self.location.href).searchParams.get('locale') === 'hi' ? 'hi' : 'en';
const copy = self.inboxMessages[locale];
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
    const snapshotLocale = event.data.locale === 'hi' ? 'hi' : 'en';
    const text = self.inboxMessages[snapshotLocale];
    const label = value => text[value] ?? value;
    const list = (conversations || []).map(row =>
      '<li>' + escapeText(row.user) + ' · ' + escapeText(label(row.status)) + '</li>').join('');
    const transcript = (messages || []).map(row =>
      '<li><strong>' + escapeText(row.kind === 'note' ? text.internalNote : label(row.role)) +
      '</strong><p>' + escapeText(row.content) + '</p></li>').join('');
    const html = '<!doctype html><html lang="' + snapshotLocale + '"><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escapeText(text.offlineTitle) + '</title>' +
      '<style>body{font:16px system-ui;max-width:50rem;margin:auto;padding:1rem}' +
      'li{margin:1rem 0;overflow-wrap:anywhere}p{white-space:pre-wrap}</style>' +
      '<h1>' + escapeText(text.offlineTitle) + '</h1><p>' + escapeText(workspace) +
      '</p><p>' + escapeText(text.offlineHelp) + '</p>' +
      '<a href="/dashboard/inbox">' + escapeText(text.reconnect) + '</a><h2>' + escapeText(text.conversations) + '</h2><ul>' + list +
      '</ul><h2>' + escapeText(text.lastOpen) + '</h2><ol>' + transcript + '</ol></html>';
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
    return new Response(copy.reconnectHelp, {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' }, status: 503,
    });
  }));
});

self.addEventListener('push', event => {
  let workspace = '';
  try { workspace = event.data?.json()?.workspace_id || ''; } catch { /* Use the current workspace. */ }
  event.waitUntil(self.registration.showNotification(copy.pushTitle, {
    body: copy.pushBody,
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
