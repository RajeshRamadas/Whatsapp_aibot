'use client';

import Script from 'next/script';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import copy from '@/messages/whatsapp-signup.en.json';

type Channel = { external_id: string; display_name: string | null; status: string };
type Config = { enabled: boolean; app_id: string; config_id: string; version: string; channel: Channel | null };
type MetaSdk = {
  init: (options: { appId: string; version: string }) => void;
  login: (callback: (result: { authResponse?: { code?: string } }) => void,
    options: { config_id: string; response_type: string; override_default_response_type: boolean;
      extras: { sessionInfoVersion: string } }) => void;
};
type Attempt = { code?: string; waba_id?: string; phone_number_id?: string; pin: string };
const sdk = () => (window as Window & { FB?: MetaSdk }).FB;
const path = 'whatsapp/signup';
const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';

export function WhatsAppSignup({ workspaceId, onConnected }: { workspaceId: string; onConnected: () => void }) {
  const [config, setConfig] = useState<Config>();
  const [sdkReady, setSdkReady] = useState(false);
  const [pin, setPin] = useState('');
  const [recipient, setRecipient] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');
  const attempt = useRef<Attempt | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    api<Config>(path + '/config', { headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setConfig(result); })
      .catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => {
      mounted.current = false;
      controller.abort();
      clearTimeout(timer.current);
      attempt.current = null;
    };
  }, [workspaceId]);

  const finish = useCallback(() => {
    const pending = attempt.current;
    if (!pending?.code || !pending.waba_id || !pending.phone_number_id) return;
    attempt.current = null;
    clearTimeout(timer.current);
    void api<Channel>(path, {
      method: 'POST', headers: { 'X-Workspace-Id': workspaceId }, body: JSON.stringify(pending),
    }).then(channel => {
      if (!mounted.current) return;
      setConfig(previous => previous ? { ...previous, channel } : previous);
      setPin('');
      setFeedback(copy.connected);
      onConnected();
    }).catch(failure => {
      if (mounted.current) setError(failure instanceof Error ? failure.message : copy.metaError);
    }).finally(() => { if (mounted.current) setBusy(false); });
  }, [workspaceId, onConnected]);

  useEffect(() => {
    function receive(event: MessageEvent) {
      if (!attempt.current || !['https://www.facebook.com', 'https://web.facebook.com'].includes(event.origin)) return;
      let data;
      try { data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; }
      catch { return; }
      if (data?.type !== 'WA_EMBEDDED_SIGNUP') return;
      if (data.event === 'CANCEL' || data.event === 'ERROR') {
        attempt.current = null;
        clearTimeout(timer.current);
        setBusy(false);
        setError(data.event === 'CANCEL' ? copy.cancelled : copy.metaError);
        return;
      }
      if (data.event !== 'FINISH') return;
      const { waba_id, phone_number_id } = data.data ?? {};
      if (typeof waba_id !== 'string' || typeof phone_number_id !== 'string') return;
      attempt.current.waba_id = waba_id;
      attempt.current.phone_number_id = phone_number_id;
      finish();
    }

    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [finish]);

  function start() {
    const fb = sdk();
    if (!config?.enabled || !fb || !/^\d{6}$/.test(pin) || busy) return;
    setBusy(true);
    setError('');
    setFeedback('');
    const pending = { pin } as Attempt;
    attempt.current = pending;
    timer.current = setTimeout(() => {
      attempt.current = null;
      setBusy(false);
      setError(copy.timeout);
    }, 300000);
    try {
      fb.init({ appId: config.app_id, version: config.version });
      fb.login(result => {
        if (attempt.current !== pending) return;
        if (!result.authResponse?.code) {
          attempt.current = null;
          clearTimeout(timer.current);
          setBusy(false);
          setError(copy.cancelled);
          return;
        }
        pending.code = result.authResponse.code;
        finish();
      }, { config_id: config.config_id, response_type: 'code', override_default_response_type: true,
        extras: { sessionInfoVersion: '3' } });
    } catch {
      attempt.current = null;
      clearTimeout(timer.current);
      setBusy(false);
      setError(copy.metaError);
    }
  }

  async function sendTest() {
    if (busy) return;
    setBusy(true);
    setFeedback('');
    setError('');
    try {
      await api(path + '/test-message', {
        method: 'POST', headers: { 'X-Workspace-Id': workspaceId }, body: JSON.stringify({ recipient }),
      });
      if (mounted.current) setFeedback(copy.testAccepted);
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : copy.metaError);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return <section className="mt-6 space-y-3 rounded-xl border border-border p-4" aria-label={copy.title}>
    <h2 className="font-semibold">{copy.title}</h2>
    {!config && !error && <p role="status">{copy.loading}</p>}
    {config?.enabled && <Script src="https://connect.facebook.net/en_US/sdk.js" onReady={() => setSdkReady(!!sdk())}
      onError={() => setError(copy.sdkError)} />}
    {config && !config.enabled && <p>{copy.unavailable}</p>}
    {config?.channel && <p className="break-words">
      {config.channel.display_name || config.channel.external_id} · {config.channel.status === 'configured'
        ? copy.configured : copy.unavailable}
    </p>}
    {config?.enabled && <form className="grid max-w-md gap-2" onSubmit={event => { event.preventDefault(); start(); }}>
      <p>{copy.precheck}</p>
      <label className="grid gap-1">{copy.pin}
        <input className={field} type="password" inputMode="numeric" autoComplete="new-password" maxLength={6}
          value={pin} onChange={event => setPin(event.target.value)} />
      </label>
      <button className={field} disabled={!sdkReady || !/^\d{6}$/.test(pin) || busy}>{copy.connect}</button>
    </form>}
    {config?.channel?.status === 'configured' && <form className="grid max-w-md gap-2"
      onSubmit={event => { event.preventDefault(); void sendTest(); }}>
      <label className="grid gap-1">{copy.recipient}
        <input className={field} type="tel" autoComplete="tel" value={recipient}
          onChange={event => setRecipient(event.target.value)} />
      </label>
      <p className="text-sm">{copy.testHint}</p>
      <button className={field} disabled={busy || !/^\+?[1-9][0-9]{6,14}$/.test(recipient)}>
        {copy.test}
      </button>
    </form>}
    {error && <p role="alert">{error}</p>}
    {(feedback || busy) && <p role="status">{feedback || copy.working}</p>}
  </section>;
}
