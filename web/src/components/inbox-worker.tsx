'use client';

import { useEffect } from 'react';

export function InboxWorker() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/inbox-sw.js', { scope: '/dashboard/' })
        .catch(error => console.warn('Inbox service worker registration failed', error));
    }
  }, []);
  return null;
}
