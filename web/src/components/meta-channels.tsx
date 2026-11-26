'use client';
import { useEffect, useState } from 'react';
import { metaClient, type MetaChannel } from '@/api/meta-dm';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
import { ApiRequestError } from '@/lib/api';

type AccountError = 'actionError' | 'conflictError' | 'busyError' | 'unavailableError' | 'permissionError' | 'validationError' | 'cleanupError' | 'sessionError';

export function MetaChannels({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('metaChannels');
  const [rows, setRows] = useState<MetaChannel[] | null>(null), [version, refresh] = useState(0);
  const [loadError, setLoadError] = useState(false), [error, setError] = useState<AccountError | null>(null), [busy, setBusy] = useState(false);
  const [kind, setKind] = useState('instagram'), [asset, setAsset] = useState(''), [token, setToken] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setRows(null); setLoadError(false); setError(null); setToken(''); setAsset('');
    metaClient.list(controller.signal).then(data => { if (!controller.signal.aborted) setRows(data.filter(row => ['instagram', 'messenger'].includes(row.kind))); })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  const available = ['instagram', 'messenger'].filter(value => !rows?.some(row => row.kind === value));
  const selectedKind = available.includes(kind) ? kind : available[0] ?? 'instagram';
  async function act(fn: () => Promise<unknown>, operation: 'connect' | 'disconnect' = 'connect') {
    setBusy(true); setError(null);
    try { await fn(); refresh(value => value + 1); } catch (failure) {
      const errors: Partial<Record<number, AccountError>> = { 401: 'sessionError', 403: 'sessionError',
        409: operation === 'disconnect' ? 'busyError' : 'conflictError', 422: 'validationError',
        424: 'cleanupError', 502: 'permissionError', 503: 'unavailableError' };
      setError(failure instanceof ApiRequestError ? errors[failure.status] ?? 'actionError' : 'actionError');
    }
    finally { setToken(''); setBusy(false); }
  }
  return <section className="mt-6 grid min-w-0 gap-4" aria-label={copy.title}>
    <p>{copy.help}</p>
    <Button className="w-fit" variant="secondary" disabled={busy} onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!rows && !loadError && <p role="status">{copy.loading}</p>}
    {loadError && <p role="alert">{copy.loadError}</p>}{error && <p role="alert">{copy[error]}</p>}
    {rows?.length === 0 && <p>{copy.empty}</p>}
    {rows?.map(row => <section key={row.id} className="grid min-w-0 gap-3 rounded-xl border border-border p-4" aria-label={row.kind === 'instagram' ? copy.instagram : copy.messenger}>
      <h2 className="text-lg font-semibold">{row.kind === 'instagram' ? copy.instagram : copy.messenger}</h2>
      <p className="break-all">{row.display_name || row.external_id}</p><p className="break-all">{copy.asset}: {row.external_id}</p>
      <p role="status">{row.status === 'configured' ? copy.connected : copy.disconnected}</p>
      <p className="text-sm text-muted">{copy.disconnectHelp}</p>
      <Button className="w-fit" variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.disconnectPrompt)) void act(() => metaClient.disconnect(row.id), 'disconnect'); }}>{copy.disconnect}</Button>
    </section>)}
    {rows && available.length > 0 && <form aria-label={copy.connectTitle} className="grid min-w-0 max-w-xl gap-4 rounded-xl border border-border p-4" onSubmit={event => {
      event.preventDefault(); void act(() => metaClient.connect({ kind: selectedKind, external_id: asset, access_token: token }));
    }}>
      <h2 className="text-lg font-semibold">{copy.connectTitle}</h2>
      <label className="grid gap-1">{copy.channel}<select className={field} disabled={busy} value={selectedKind} onChange={e => { setKind(e.target.value); setToken(''); setAsset(''); }}>
        {available.map(value => <option key={value} value={value}>{value === 'instagram' ? copy.instagram : copy.messenger}</option>)}
      </select></label>
      <label className="grid gap-1">{copy.asset}<input className={field} disabled={busy} required inputMode="numeric" pattern="[0-9]{1,32}" maxLength={32} autoComplete="off" value={asset} onChange={e => setAsset(e.target.value)} /></label>
      <label className="grid gap-1">{copy.token}<input className={field} disabled={busy} required type="password" maxLength={8192} autoComplete="new-password" value={token} onChange={e => setToken(e.target.value)} /></label>
      <p className="text-sm text-muted">{copy.tokenHelp}</p>
      <Button disabled={busy || !asset || !token}>{busy ? copy.verifying : copy.connect}</Button>
    </form>}
  </section>;
}
