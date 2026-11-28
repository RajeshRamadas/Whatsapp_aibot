'use client';
import { useEffect, useRef, useState } from 'react';
import { metaOAuthClient as client, type MetaAuthorizationView, type MetaKind } from '@/api/meta-oauth';
import type { MetaChannel } from '@/api/meta-dm';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
const terminal = ['complete', 'failed', 'denied', 'cancelled', 'expired'];
function clearReturn() {
  const url = new URL(window.location.href); url.searchParams.delete('meta_oauth');
  window.history.replaceState(null, '', url.pathname + url.search + url.hash);
}
export function MetaAuthorization({ available, accounts = [], onConnected }: {
  available: string[];
  accounts?: MetaChannel[];
  onConnected: () => void;
}) {
  const copy = useCopy('metaChannels');
  const [configured, setConfigured] = useState<boolean | null>(null), [error, setError] = useState(false);
  const [identity, setIdentity] = useState(''), [view, setView] = useState<MetaAuthorizationView | null>(null);
  const [target, setTarget] = useState('instagram'), [account, setAccount] = useState('');
  const [busy, setBusy] = useState(false), [revision, retry] = useState(0);
  const lifecycle = useRef<AbortController | null>(null), notified = useRef('');
  const connected = useRef(onConnected); connected.current = onConnected;
  useEffect(() => {
    const controller = new AbortController(); lifecycle.current = controller;
    client.config(controller.signal).then(data => { if (!controller.signal.aborted) setConfigured(data.configured); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    const returned = new URL(window.location.href).searchParams.get('meta_oauth');
    if (returned && /^[a-f0-9-]{36}$/i.test(returned)) setIdentity(returned);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!identity) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await client.status(identity, controller.signal);
        if (controller.signal.aborted) return;
        setView(result); setError(false);
        if (result.status === 'complete' && notified.current !== identity) {
          notified.current = identity; clearReturn(); connected.current();
        }
        if (!terminal.includes(result.status) && result.status !== 'selecting') timer = setTimeout(poll, 1000);
      } catch { if (!controller.signal.aborted) setError(true); }
    }
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [identity, revision]);
  const options = [...available.map(kind => ({ value: kind, kind, channelId: undefined })),
    ...accounts.map(row => ({ value: row.id, kind: row.kind, channelId: row.id }))];
  const selected = options.find(option => option.value === target) ?? options[0];
  const active = !!identity && (!view || !terminal.includes(view.status));
  async function act(operation: 'start' | 'select' | 'cancel') {
    if (operation === 'start' && !selected) return;
    const signal = lifecycle.current?.signal; setBusy(true); setError(false);
    try {
      if (operation === 'start' && selected) {
        const result = await client.start(selected.kind as MetaKind, signal, selected.channelId);
        if (signal?.aborted) return;
        setView(null); setAccount(''); setIdentity(result.id); window.location.assign(result.url);
      } else {
        const result = operation === 'select' ? await client.select(identity, account, signal) : await client.cancel(identity, signal);
        if (signal?.aborted) return;
        setView(result); retry(value => value + 1);
        if (operation === 'cancel') clearReturn();
      }
    } catch { if (!signal?.aborted) setError(true); }
    finally { if (!signal?.aborted) setBusy(false); }
  }
  const reason = view?.error;
  const failure = reason === 'account_not_authorized' ? copy.oauthWrongAccount :
    reason === 'no_accounts' ? copy.oauthNoAccounts : reason === 'cleanup_required' ? copy.oauthCleanup :
    reason === 'connection_conflict' ? copy.conflictError : reason === 'authorization_changed' ? copy.sessionError :
    reason === 'setup_unavailable' ? copy.unavailableError : copy.oauthFailed;
  return <section className="grid min-w-0 max-w-xl gap-3 rounded-xl border border-border p-4" aria-label={copy.oauthTitle}>
    <h2 className="text-lg font-semibold">{copy.oauthTitle}</h2><p>{copy.oauthHelp}</p>
    {configured === false && <p>{copy.oauthSetup}</p>}
    {error && <><p role="alert">{copy.oauthError}</p><Button variant="secondary" disabled={busy} onClick={() => { if (identity) retry(v => v + 1); else window.location.reload(); }}>{copy.oauthRetry}</Button></>}
    {view && <p role="status">{view.status === 'complete' ? copy.oauthComplete : view.status === 'denied' ? copy.oauthDenied :
      view.status === 'cancelled' ? copy.oauthCancelled : view.status === 'expired' ? copy.oauthExpired :
      view.status === 'failed' ? failure : view.status === 'selecting' ? copy.oauthChoose : copy.oauthWaiting}</p>}
    {active && view?.status === 'selecting' && <form className="grid min-w-0 gap-3" aria-label={copy.oauthChoose} onSubmit={event => { event.preventDefault(); void act('select'); }}>
      <label className="grid gap-1">{copy.oauthAccount}<select className={field} required disabled={busy} value={account} onChange={event => setAccount(event.target.value)}>
        <option value="">{copy.oauthChoose}</option>{view.accounts.map(row => <option key={row.external_id} value={row.external_id}>{row.name} ({row.external_id})</option>)}
      </select></label><Button disabled={busy || !account}>{copy.oauthConnect}</Button>
    </form>}
    {active && <Button variant="secondary" disabled={busy} onClick={() => void act('cancel')}>{copy.oauthCancel}</Button>}
    {!active && selected && <>
      <label className="grid gap-1">{copy.oauthChannel}<select className={field} value={selected.value} disabled={busy} onChange={event => setTarget(event.target.value)}>
        {options.map(option => <option key={option.value} value={option.value}>
          {option.kind === 'instagram' ? copy.instagram : copy.messenger}{option.channelId ? ` (${copy.oauthReauthorize})` : ''}
        </option>)}
      </select></label><Button disabled={configured !== true || busy} onClick={() => void act('start')}>
        {selected.channelId ? copy.oauthReauthorize : copy.oauthAuthorize}
      </Button>
    </>}
  </section>;
}
