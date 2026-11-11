'use client';
import { useEffect, useState } from 'react';
import { actionClient, jsonObject, type CustomAction, type ActionCall } from '@/api/http-actions';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
import { HttpActionForm } from './http-action-form';
export function HttpActions({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('actions'), locale = useDisplayLocale();
  const [rows, setRows] = useState<CustomAction[]>(), [loadError, setLoadError] = useState(false), [version, refresh] = useState(0);
  const [editing, setEditing] = useState<CustomAction | 'new'>(), [busy, setBusy] = useState(false), [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setLoadError(false);
    actionClient.list(controller.signal).then(setRows).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  async function act(fn: () => Promise<unknown>) { setBusy(true); setError(false); try { await fn(); refresh(v => v + 1); } catch { setError(true); } finally { setBusy(false); } }
  return <section className="mt-6 min-w-0 space-y-5" aria-label={copy.manage}>
    <p className="text-sm text-muted">{copy.policy}</p>{error && <p role="alert">{copy.actionError}</p>}
    <div className="flex flex-wrap gap-3"><Button disabled={busy} onClick={() => setEditing('new')}>{copy.add}</Button><Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button></div>
    {editing && <HttpActionForm key={editing === 'new' ? 'new' : editing.id} row={editing === 'new' ? undefined : editing} onCancel={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); refresh(v => v + 1); }} />}
    {!rows && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError}</p>}{rows?.length === 0 && <p>{copy.empty}</p>}
    {rows?.map(row => <section key={row.id} aria-label={row.name} className="min-w-0 space-y-3 rounded-xl border border-border p-4">
      <h2 className="break-words text-xl font-semibold">{row.name}</h2><p className="break-words">{String(row.definition.description)}</p><p className="break-all font-mono text-sm">{String(row.definition.method)} {String(row.definition.url_template)}</p>
      <p>{row.enabled ? copy.enabled : row.tested_revision !== row.revision ? copy.needsTest : copy.paused}</p>
      <p className="text-sm">{copy.lastSuccess}: {row.last_success_at ? new Date(row.last_success_at).toLocaleString(locale, { timeZone: 'Asia/Calcutta' }) : copy.never}</p>
      {row.last_error && <p role="status">{copy.failure} <span className="font-mono text-sm">{row.last_error}</span></p>}
      {row.credential_last4 && <p className="text-sm">{copy.credentialHint}: …{row.credential_last4}</p>}
      <div className="flex flex-wrap gap-3"><Button disabled={busy || !row.enabled && (row.tested_revision !== row.revision || row.status !== 'connected')} onClick={() => void act(() => actionClient.enable(row.id, !row.enabled))}>{row.enabled ? copy.pause : copy.enable}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => setEditing(row)}>{copy.edit}</Button><Button variant="destructive" disabled={busy} onClick={() => { if (window.confirm(copy.deletePrompt)) void act(() => actionClient.remove(row.id)); }}>{copy.remove}</Button></div>
      <ActionTest row={row} revision={version} onTested={() => refresh(v => v + 1)} />
    </section>)}
  </section>;
}
function ActionTest({ row, revision, onTested }: { row: CustomAction; revision: number; onTested: () => void }) {
  const copy = useCopy('actions'), locale = useDisplayLocale();
  const [values, setValues] = useState('{}'), [phone, setPhone] = useState(''), [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState<ActionCall>(), [calls, setCalls] = useState<ActionCall[]>(), [historyError, setHistoryError] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(false), [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setHistoryError(false);
    actionClient.calls(row.id, controller.signal).then(setCalls).catch(() => { if (!controller.signal.aborted) setHistoryError(true); });
    return () => controller.abort();
  }, [row.id, revision, open]);
  useEffect(() => { setResult(undefined); }, [row.revision]);
  const changes = row.definition.method !== 'GET';
  return <details onToggle={e => setOpen(e.currentTarget.open)} className="min-w-0 border-t border-border pt-3"><summary className="min-h-11 cursor-pointer font-semibold">{copy.testTitle}</summary>
    {open && <div className="min-w-0 space-y-4"><p>{copy.testHelp}</p><form className="space-y-3" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError(false); setResult(undefined);
      try { const response = await actionClient.test(row.id, { values: jsonObject(values), request_key: crypto.randomUUID(), sample_phone: phone || null, confirm_changes: confirm }); setResult(response); onTested(); }
      catch { setError(true); } finally { setBusy(false); }
    }}>
      <label className="grid gap-1">{copy.sample}<textarea className={field + ' font-mono'} rows={3} required value={values} onChange={e => setValues(e.target.value)} spellCheck={false} /></label>
      {row.definition.identity === 'phone_match' && <label className="grid gap-1">{copy.samplePhone}<input className={field} type="tel" pattern="\+?[1-9][0-9]{6,14}" value={phone} onChange={e => setPhone(e.target.value)} /></label>}
      {changes && <label className="flex min-h-11 items-start gap-3"><input className="mt-1" type="checkbox" required checked={confirm} onChange={e => setConfirm(e.target.checked)} />{copy.testChanges}</label>}
      <Button disabled={busy || changes && !confirm}>{busy ? copy.testing : copy.test}</Button>{error && <p role="alert">{copy.testError}</p>}
    </form>
    {result && <section aria-label={copy.testResult} className="min-w-0 space-y-3"><h3 className="font-semibold">{copy.testResult}</h3><p role="status">{result.state === 'complete' ? copy.testPassed : copy.testFailed}</p>
      {result.error && <p className="break-words font-mono text-sm">{result.error}</p>}{result.identity && <p>{copy.identityResult}: {result.identity}</p>}
      <h4 className="font-medium">{copy.projection}</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-border p-3 text-sm">{JSON.stringify(result.data ?? null, null, 2)}</pre>
      <details><summary className="min-h-11 cursor-pointer">{copy.raw}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-border p-3 text-sm">{JSON.stringify(result.raw ?? null, null, 2)}</pre></details>
      <Button variant="secondary" onClick={() => setResult(undefined)}>{copy.clearResult}</Button>
    </section>}
    <h3 className="font-semibold">{copy.history}</h3>{historyError && <p role="alert">{copy.historyError}</p>}{calls?.length === 0 && <p>{copy.noCalls}</p>}
    <ul className="space-y-2 text-sm">{calls?.map(call => <li key={call.id} className="break-words rounded border border-border p-2">{call.created_at && new Date(call.created_at).toLocaleString(locale, { timeZone: 'Asia/Calcutta' })} · {copy.states[call.state as keyof typeof copy.states] ?? call.state} · HTTP {call.http_status ?? '—'} · {call.duration_ms ?? '—'} ms{call.error && <span className="block font-mono">{call.error}</span>}</li>)}</ul>
    </div>}
  </details>;
}
