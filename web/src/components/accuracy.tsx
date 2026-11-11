'use client';
import { useEffect, useMemo, useState } from 'react';
import { accuracyClient, type AccuracyDetail, type Act } from '@/api/accuracy';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
import { AccuracyCases } from './accuracy-cases';
import { AccuracyHistory } from './accuracy-history';
export function Accuracy({ workspaceId, admin }: { workspaceId: string; admin: boolean }) {
  const copy = useCopy('accuracy'), api = useMemo(() => accuracyClient(workspaceId), [workspaceId]);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.load>>>(), [loadError, setLoadError] = useState(false);
  const [detail, setDetail] = useState<AccuracyDetail>(), [detailError, setDetailError] = useState(false), [selected, setSelected] = useState('');
  const [caseIds, setCaseIds] = useState<string[]>([]), [label, setLabel] = useState(''), [paid, setPaid] = useState(false), [key, setKey] = useState('');
  const [offset, setOffset] = useState(0), [revision, refresh] = useState(0), [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setKey(crypto.randomUUID()); }, []);
  useEffect(() => {
    const controller = new AbortController();
    api.load(offset, controller.signal).then(v => { if (!controller.signal.aborted) { setData(v); setLoadError(false); setSelected(id => id || v.runs[0]?.id || ''); } })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    const timer = setTimeout(() => refresh(v => v + 1), 10000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, offset, revision]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    api.detail(selected, controller.signal).then(v => { if (!controller.signal.aborted) { setDetail(v); setDetailError(false); } })
      .catch(() => { if (!controller.signal.aborted) setDetailError(true); });
    return () => controller.abort();
  }, [api, selected, revision]);
  const act: Act = async operation => {
    setBusy(true); setError(false); setSaved(false);
    try { await operation(); setSaved(true); refresh(v => v + 1); return true; }
    catch { setError(true); return false; }
    finally { setBusy(false); }
  };
  const active = data?.runs.find(r => ['queued', 'running'].includes(r.state));
  const available = caseIds.filter(id => data?.cases.some(c => c.id === id));
  return <div className="mt-5 min-w-0 space-y-6">
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError} <Button onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></p>}
    {error && <p role="alert">{copy.error}</p>}{saved && <p role="status">{copy.saved}</p>}
    {data && <>
      <AccuracyCases cases={data.cases} selected={available} select={ids => { setCaseIds(ids); setKey(crypto.randomUUID()); }} max={data.max_run_cases} admin={admin} api={api} act={act} busy={busy} />
      {admin && <form aria-label={copy.start} className="grid gap-3 rounded-xl border border-border p-4" onSubmit={async e => {
        e.preventDefault(); await act(async () => { const value = await api.start({ label, case_ids: available, request_key: key }); setSelected(value.id); setDetail(undefined); setKey(crypto.randomUUID()); setPaid(false); setOffset(0); });
      }}><h2 className="text-xl font-semibold">{copy.start}</h2><p className="text-sm text-muted">{copy.paidHelp}</p>
        <label className="grid gap-1">{copy.runLabel}<input className={field} required maxLength={100} value={label} disabled={busy} onChange={e => { setLabel(e.target.value); setKey(crypto.randomUUID()); }} /></label>
        <p>{copy.selection.replace('{count}', String(available.length)).replace('{max}', String(data.max_run_cases))}</p>
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" required checked={paid} disabled={busy} onChange={e => setPaid(e.target.checked)} />{copy.approve}</label>
        <Button disabled={busy || !key || !paid || available.length === 0 || Boolean(active)}>{copy.start}</Button>
        {active && <><p>{copy.active}</p><Button type="button" variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.cancelPrompt)) void act(() => api.cancel(active.id)); }}>{copy.cancelRun}</Button></>}
      </form>}
      <AccuracyHistory runs={data.runs} detail={detail} selected={selected} choose={id => { setSelected(id); setDetail(undefined); refresh(v => v + 1); }} offset={offset} page={setOffset} busy={busy} detailError={detailError} />
    </>}
  </div>;
}
