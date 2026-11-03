self.addEventListener('push', event => {
  event.waitUntil(self.registration.showNotification('Inbox needs attention', {
    body: 'Open the inbox to review the latest activity.',
    icon: '/inbox-icon.svg',
    tag: 'inbox-attention',
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = new URL('/dashboard/inbox', self.location.origin).href;
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const inbox = windows.find(client => client.url === url);
    if (inbox) await inbox.focus();
    else await self.clients.openWindow(url);
  })());
});
