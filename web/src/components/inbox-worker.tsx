'use client';

import { useEffect, useState } from 'react';
import { useWorkspace } from '@/components/app-shell';
import { api } from '@/lib/api';

export function InboxWorker() {
  const workspace = useWorkspace();
  const [registration, setRegistration] = useState<ServiceWorkerRegistration>();
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/inbox-sw.js', { scope: '/dashboard/' })
        .then(() => navigator.serviceWorker.ready)
        .then(setRegistration)
        .catch(() => setFeedback('Inbox notifications are unavailable in this browser.'));
    }
  }, []);

  async function changeSubscription(enable: boolean) {
    if (!registration || busy) return;
    setBusy(true);
    setFeedback('');
    try {
      let subscription = await registration.pushManager.getSubscription();
      if (enable) {
        const { public_key } = await api<{ public_key: string | null }>('inbox/push-config');
        if (!public_key) throw new Error('Push notifications are unavailable on this server.');
        subscription ??= await registration.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: public_key,
        });
        await api('inbox/push-subscription', { method: 'PUT', body: JSON.stringify(subscription) });
      } else if (subscription) {
        await api('inbox/push-subscription', {
          method: 'DELETE', body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
      }
      setFeedback(enable ? 'Notifications enabled for this workspace.' : 'Notifications disabled for this workspace.');
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'Could not update notifications.');
    } finally {
      setBusy(false);
    }
  }

  if (!workspace || workspace.role === 'viewer' || !registration || !('pushManager' in registration)) return null;
  return <section aria-label="Inbox notifications" className="px-4 pt-4 text-sm">
    <button className="mr-4 underline" disabled={busy} onClick={() => void changeSubscription(true)}>
      Enable notifications
    </button>
    <button className="underline" disabled={busy} onClick={() => void changeSubscription(false)}>
      Disable notifications
    </button>
    <p role="status">{feedback}</p>
  </section>;
}
