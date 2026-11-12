'use client';
import { useEffect, useState } from 'react';
import { knowledgeSyncClient as client, type SyncCatalog } from '@/api/knowledge-sync';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';

export function KnowledgeSync({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('knowledgeSync'), locale = useDisplayLocale();
  const [data, setData] = useState<SyncCatalog>(), [version, refresh] = useState(0);
  const [loadError, setLoadError] = useState(false), [error, setError] = useState(false), [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState('notion'), [label, setLabel] = useState(''), [selection, setSelection] = useState('');
  const [token, setToken] = useState(''), [readOnly, setReadOnly] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setLoadError(false);
    client.catalog(controller.signal).then(setData).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  const active = data?.connections.some(c => c.enabled && !c.last_error);
  useEffect(() => { if (!active) return; const timer = setInterval(() => refresh(v => v + 1), 5000); return () => clearInterval(timer); }, [active]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(false); try { await fn(); refresh(v => v + 1); } catch { setError(true); } finally { setBusy(false); }
  }
  const selected = selection.split(/[\s,]+/).filter(Boolean);
  const date = (value: string | null) => value ? new Date(value).toLocaleString(locale) : copy.never;
  return <section aria-label={copy.title} className="mt-6 min-w-0 space-y-5">
    <h2 className="text-xl font-semibold">{copy.title}</h2><p>{copy.intro}</p><p className="text-sm text-muted">{copy.privacy}</p>
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError}</p>}{error && <p role="alert">{copy.actionError}</p>}
    {data?.connections.length === 0 && <p>{copy.empty}</p>}
    {data?.connections.map(c => <section key={c.id} aria-label={c.label} className="min-w-0 space-y-3 rounded-xl border border-border p-4">
      <h3 className="break-words text-lg font-semibold">{c.label}</h3><p>{c.provider === 'drive' ? copy.drive : copy.notion}</p>
      <p role="status">{!c.enabled ? copy.paused : c.last_error ? copy.attention : c.lease ? copy.syncing : c.last_success ? copy.connected : copy.pending}</p>
      <p>{copy.lastSync}: {date(c.last_success)}</p><p>{copy.nextSync}: {date(c.next_run)}</p>
      {c.last_error && <p role="alert">{copy.syncError}</p>}
      <details><summary className="min-h-11 cursor-pointer">{copy.selected}</summary><ul className="space-y-2 break-all text-sm">{c.selection.map(id => <li key={id}>{id}</li>)}</ul></details>
      <p>{copy.fileCount}: {data.sources.filter(s => s.sync_connection_id === c.id).length}</p>
      <ul className="space-y-2">{data.sources.filter(s => s.sync_connection_id === c.id).map(s => <li key={s.id} className="break-words">{s.filename} · {s.status === 'ready' ? copy.ready : copy.processing}</li>)}</ul>
      <div className="flex flex-wrap gap-3">
        <Button disabled={busy || !!c.lease || !c.enabled || c.status === 'requested' || c.status === 'needs_attention'} onClick={() => void act(() => client.change(c.id, 'sync'))}>{copy.sync}</Button>
        <Button variant="secondary" disabled={busy || c.status === 'requested'} onClick={() => void act(() => client.change(c.id, c.enabled ? 'pause' : 'resume'))}>{c.enabled ? copy.pause : copy.resume}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.disconnectPrompt)) void act(() => client.disconnect(c.id)); }}>{copy.disconnect}</Button>
      </div>
    </section>)}
    {data && <form className="grid max-w-lg gap-4" onSubmit={e => { e.preventDefault(); void act(async () => {
      if (provider === 'drive') { const result = await client.drive({ label, folders: selected }); window.location.assign(result.url); }
      else { await client.notion({ label, pages: selected, token, read_only: readOnly }); setToken(''); setSelection(''); setLabel(''); setReadOnly(false); }
    }); }}>
      <h3 className="text-lg font-semibold">{copy.add}</h3><p className="text-sm text-muted">{copy.replace}</p>
      <label className="grid gap-1">{copy.provider}<select className={field} value={provider} onChange={e => { setProvider(e.target.value); setToken(''); setSelection(''); setReadOnly(false); }}><option value="notion">{copy.notion}</option><option value="drive">{copy.drive}</option></select></label>
      <label className="grid gap-1">{copy.label}<input required maxLength={100} className={field} value={label} onChange={e => setLabel(e.target.value)} /></label>
      <label className="grid gap-1">{provider === 'drive' ? copy.folders : copy.pages}<textarea required maxLength={4096} rows={3} className={field + ' break-all'} value={selection} onChange={e => setSelection(e.target.value)} /></label>
      <p className="text-sm text-muted">{provider === 'drive' ? copy.driveHelp : copy.notionHelp}</p>
      {provider === 'notion' && <><label className="grid gap-1">{copy.token}<input type="password" required minLength={16} maxLength={4096} autoComplete="new-password" className={field} value={token} onChange={e => setToken(e.target.value)} /></label>
        <label className="flex min-h-11 items-center gap-3"><input type="checkbox" required checked={readOnly} onChange={e => setReadOnly(e.target.checked)} />{copy.readOnly}</label></>}
      {provider === 'drive' && !data.drive_configured && <p role="status">{copy.notConfigured}</p>}
      <Button disabled={busy || data.connections.some(c => c.provider === provider) || (provider === 'drive' ? !data.drive_configured : !readOnly)}>{busy ? copy.saving : provider === 'drive' ? copy.authorize : copy.connect}</Button>
    </form>}
  </section>;
}
