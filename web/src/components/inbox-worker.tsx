'use client';

import { useEffect, useState } from 'react';
import { useWorkspace } from '@/components/app-shell';
import { api } from '@/lib/api';
import messages from '@/messages/en.json';

export function InboxWorker() {
  const workspace = useWorkspace();
  const [registration, setRegistration] = useState<ServiceWorkerRegistration>();
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const canAct = !!workspace && workspace.role !== 'viewer';

  useEffect(() => {
    let active = true;
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/inbox-sw.js', { scope: '/dashboard/' })
        .then(() => navigator.serviceWorker.ready)
        .then(value => { if (active) setRegistration(value); })
        .catch(() => { if (active) setFeedback(messages.pushUnavailable); });
    }
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!registration || !workspace || !canAct || !('pushManager' in registration)) return;
    const controller = new AbortController();
    const headers = { 'X-Workspace-Id': workspace.id };
    async function readStatus() {
      try {
        const config = await api<{ public_key: string | null }>('inbox/push-config', {
          headers, signal: controller.signal,
        });
        const subscription = await registration!.pushManager.getSubscription();
        const state = subscription ? await api<{ enabled: boolean }>(
          'inbox/push-subscription?endpoint=' + encodeURIComponent(subscription.endpoint),
          { headers, signal: controller.signal },
        ) : { enabled: false };
        if (!controller.signal.aborted) {
          setPublicKey(config.public_key);
          setEnabled(state.enabled);
        }
      } catch {
        if (!controller.signal.aborted) setFeedback(messages.pushUnavailable);
      }
    }
    void readStatus();
    return () => controller.abort();
  }, [registration, workspace, canAct]);

  async function changeSubscription(enable: boolean) {
    if (!registration || !workspace || busy) return;
    setBusy(true);
    setFeedback('');
    const headers = { 'X-Workspace-Id': workspace.id };
    try {
      if (enable && Notification.permission !== 'granted') {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') throw new Error(messages.pushDenied);
      }
      let subscription = await registration.pushManager.getSubscription();
      if (enable) {
        if (!publicKey) throw new Error(messages.pushUnavailable);
        const padded = publicKey.replace(/-/g, '+').replace(/_/g, '/');
        const key = Uint8Array.from(atob(padded.padEnd(Math.ceil(padded.length / 4) * 4, '=')),
          char => char.charCodeAt(0));
        subscription ??= await registration.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: key,
        });
        await api('inbox/push-subscription', { method: 'PUT', headers, body: JSON.stringify(subscription) });
      } else if (subscription) {
        // A browser subscription may serve several workspaces. Remove only this opt-in.
        await api('inbox/push-subscription', {
          method: 'DELETE', headers, body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
      }
      setEnabled(enable);
      setFeedback(enable ? messages.pushEnabled : messages.pushDisabled);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : messages.pushUnavailable);
    } finally {
      setBusy(false);
    }
  }

  if (!canAct || !registration || !('pushManager' in registration)) return null;
  return <section aria-label={messages.pushSettings} className="px-4 pt-4 text-sm">
    <p>{enabled ? messages.pushEnabled : messages.pushDisabled}</p>
    {!publicKey && <p>{messages.pushUnavailable}</p>}
    <button className="mr-4 min-h-11 underline" disabled={busy || enabled || !publicKey}
      onClick={() => void changeSubscription(true)}>{messages.pushEnable}</button>
    <button className="min-h-11 underline" disabled={busy || !enabled}
      onClick={() => void changeSubscription(false)}>{messages.pushDisable}</button>
    <p role="status">{feedback}</p>
  </section>;
}
