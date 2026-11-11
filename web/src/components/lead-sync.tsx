'use client';
import { useEffect, useMemo, useState } from 'react';
import { syncClient, type Act, type SyncData } from '@/api/lead-sync';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
import { LeadSyncConnection } from './lead-sync-connection';
export function LeadSync({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('leadSync'), api = useMemo(() => syncClient(workspaceId), [workspaceId]);
  const [data, setData] = useState<SyncData>(), [loadError, setLoadError] = useState(false), [error, setError] = useState(false);
  const [busy, setBusy] = useState(false), [saved, setSaved] = useState(false), [revision, refresh] = useState(0);
  const [provider, setProvider] = useState<'hubspot' | 'zoho'>('hubspot'), [region, setRegion] = useState<'in' | 'us' | 'eu' | 'au'>('in');
  const [label, setLabel] = useState(''), [token, setToken] = useState(''), [clientId, setClientId] = useState(''), [secret, setSecret] = useState(''), [refreshToken, setRefreshToken] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api.load(controller.signal).then(value => { if (!controller.signal.aborted) { setData(value); setLoadError(false); } })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    const timer = setTimeout(() => refresh(v => v + 1), 10000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, revision]);
  const act: Act = async operation => {
    setBusy(true); setError(false); setSaved(false);
    try { await operation(); setSaved(true); refresh(v => v + 1); return true; }
    catch { setError(true); return false; }
    finally { setBusy(false); }
  };
  return <div className="mt-5 min-w-0 space-y-5">
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}
    {loadError && <p role="alert">{copy.loadError} <Button onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></p>}
    {error && <p role="alert">{copy.error}</p>}{saved && <p role="status">{copy.saved}</p>}
    {data && <>
      <details className="rounded-xl border border-border p-4"><summary className="min-h-11 cursor-pointer font-semibold">{copy.add}</summary>
        <form aria-label={copy.add} className="grid min-w-0 gap-3 sm:grid-cols-2" onSubmit={async e => {
          e.preventDefault(); if (await act(() => api.connect({ provider, label, region,
            ...(provider === 'hubspot' ? { token } : { client_id: clientId, client_secret: secret, refresh_token: refreshToken }) }))) {
            setToken(''); setClientId(''); setSecret(''); setRefreshToken(''); setLabel('');
          }
        }}>
          <label className="grid gap-1">{copy.provider}<select className={field} value={provider} disabled={busy} onChange={e => { setProvider(e.target.value as typeof provider); setToken(''); setSecret(''); setRefreshToken(''); }}>
            <option value="hubspot">HubSpot</option><option value="zoho">Zoho CRM</option></select></label>
          <label className="grid gap-1">{copy.label}<input className={field} value={label} required maxLength={100} disabled={busy} onChange={e => setLabel(e.target.value)} /></label>
          <p className="break-words text-sm text-muted sm:col-span-2">{provider === 'hubspot' ? copy.hubHelp : copy.zohoHelp}</p>
          {provider === 'hubspot' ? <label className="grid gap-1 sm:col-span-2">{copy.token}<input className={field} type="password" autoComplete="off" required minLength={8} maxLength={8192} value={token} disabled={busy} onChange={e => setToken(e.target.value)} /></label> : <>
            <label className="grid gap-1">{copy.region}<select className={field} value={region} disabled={busy} onChange={e => setRegion(e.target.value as typeof region)}>
              {Object.entries(copy.regions).map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></label>
            {([[copy.clientId, clientId, setClientId], [copy.clientSecret, secret, setSecret], [copy.refreshToken, refreshToken, setRefreshToken]] as const).map(([title, value, setter]) =>
              <label className="grid gap-1" key={title}>{title}<input className={field} type="password" autoComplete="off" required minLength={8} maxLength={8192} value={value} disabled={busy} onChange={e => setter(e.target.value)} /></label>)}
          </>}
          <Button disabled={busy}>{copy.connect}</Button>
        </form>
        <form aria-label={copy.google} className="mt-5 grid gap-3 border-t border-border pt-4" onSubmit={e => {
          e.preventDefault(); void act(async () => { const value = await api.google(new FormData(e.currentTarget).get('label') as string); window.location.assign(value.url); });
        }}><h2 className="font-semibold">{copy.google}</h2><p className="text-sm text-muted">{copy.googleHelp}</p>
          {!data.google_configured && <p>{copy.googlePending}</p>}
          <label className="grid gap-1">{copy.googleLabel}<input className={field} name="label" required maxLength={100} disabled={busy || !data.google_configured} /></label>
          <Button disabled={busy || !data.google_configured}>{copy.google}</Button>
        </form>
      </details>
      {data.connections.length === 0 && <p>{copy.empty}</p>}
      {data.connections.map(c => <LeadSyncConnection key={c.id} connection={c} api={api} act={act} busy={busy} revision={revision} />)}
    </>}
  </div>;
}
