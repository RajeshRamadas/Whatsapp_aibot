'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import * as websites from '@/api/websites';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/primitives';
import copy from '@/messages/websites.en.json';

const field = 'min-h-11 w-full rounded-lg border border-border bg-surface p-2';
const date = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Calcutta', dateStyle: 'medium', timeStyle: 'short' });
export function WebsiteSources({ workspaceId }: { workspaceId: string }) {
  const [data, setData] = useState<websites.WebsiteList | null>(null);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState('');
  const [follow, setFollow] = useState(false);
  const [maximum, setMaximum] = useState(10);
  const [depth, setDepth] = useState(2);
  const [cadence, setCadence] = useState<websites.Schedule>('manual');
  const [confirm, setConfirm] = useState('');
  const [expanded, setExpanded] = useState('');
  const [pages, setPages] = useState<websites.Page[] | null>(null);
  const load = useCallback(async () => {
    const result = await websites.list(workspaceId, offset);
    setData(result);
  }, [workspaceId, offset]);
  useEffect(() => {
    const refresh = () => { void load().then(() => setError('')).catch(() => setError(copy.error)); };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [load]);
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : copy.error); }
    finally { setBusy(false); }
  }
  function add(event: FormEvent) {
    event.preventDefault();
    void mutate(async () => {
      await websites.create(workspaceId, { url, max_pages: follow ? maximum : 1, depth: follow ? depth : 0,
        schedule: cadence });
      setUrl('');
    });
  }
  async function show(id: string) {
    if (expanded === id) { setExpanded(''); return; }
    setExpanded(id); setPages(null);
    try { setPages(await websites.pages(workspaceId, id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : copy.error); setExpanded(''); }
  }
  const remaining = data ? Math.max(0, data.page_limit - data.reserved_pages) : 0;
  return <section aria-label={copy.title} className="mt-6 space-y-4">
    <h2 className="text-xl font-semibold">{copy.title}</h2>
    <p className="text-muted">{copy.intro}</p><p className="text-sm text-muted">{copy.policy}</p>
    {!data && !error && <p role="status">{copy.loading}</p>}
    {error && <div role="alert"><p>{error}</p><Button variant="secondary" onClick={() => {
      void load().then(() => setError('')).catch(() => setError(copy.error));
    }}>{copy.retry}</Button></div>}
    {data && <>
      <p>{copy.budget.replace('{used}', String(data.reserved_pages)).replace('{limit}', String(data.page_limit))}</p>
      {!remaining && <Link href="/dashboard/billing" className="underline">{copy.upgrade}</Link>}
      <form onSubmit={add} className="space-y-4 rounded-xl border border-border p-4">
        <label className="block">{copy.url}<input className={field} type="url" required maxLength={2048}
          value={url} onChange={event => setUrl(event.target.value)} placeholder={copy.placeholder} /></label>
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={follow}
          onChange={event => { setFollow(event.target.checked); setMaximum(Math.min(maximum, remaining || 1)); }} />{copy.follow}</label>
        {follow && <div className="grid gap-3 sm:grid-cols-2">
          <label>{copy.maximum}<input className={field} type="number" min={1} max={Math.max(1, Math.min(50, remaining))}
            required value={maximum} onChange={event => setMaximum(Number(event.target.value))} /></label>
          <label>{copy.depth}<input className={field} type="number" min={1} max={3} required value={depth}
            onChange={event => setDepth(Number(event.target.value))} /></label>
        </div>}
        <label className="block">{copy.schedule}<select className={field} value={cadence}
          onChange={event => setCadence(event.target.value as websites.Schedule)}>
          {(['manual', 'daily', 'weekly'] as const).map(value => <option key={value} value={value}>{copy[value]}</option>)}
        </select></label>
        <Button disabled={busy || remaining < (follow ? maximum : 1)}>{busy ? copy.saving : copy.add}</Button>
      </form>
      {!data.items.length && <p>{copy.empty}</p>}
      <ul className="space-y-3">{data.items.map(site => <li key={site.id} className="space-y-3 rounded-xl border border-border p-4">
        <div className="flex flex-wrap items-center gap-3"><h3 className="min-w-0 flex-1 break-all font-semibold">{site.url}</h3>
          <Badge tone={site.status === 'ready' ? 'success' : site.status === 'failed' ? 'danger' : 'neutral'}>
            {copy[site.status]}</Badge></div>
        <p>{copy.pages}: {site.pages} / {site.max_pages}</p>
        <p className="text-sm">{copy.last}: {site.last_success ? date.format(new Date(site.last_success)) : copy.never}</p>
        <p className="text-sm">{copy.next}: {site.next_run ? date.format(new Date(site.next_run)) : copy.unscheduled}</p>
        {site.error && <p role="alert">{site.error}</p>}
        <label className="block">{copy.schedule}<select className={field} value={site.schedule} disabled={busy}
          onChange={event => void mutate(() => websites.schedule(workspaceId, site.id, event.target.value as websites.Schedule))}>
          {(['manual', 'daily', 'weekly'] as const).map(value => <option key={value} value={value}>{copy[value]}</option>)}
        </select></label>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={busy || site.status === 'queued' || site.status === 'running'}
            onClick={() => { setExpanded(''); void mutate(() => websites.refresh(workspaceId, site.id)); }}>{copy.refresh}</Button>
          <Button variant="secondary" aria-expanded={expanded === site.id} onClick={() => void show(site.id)}>
            {expanded === site.id ? copy.hide : copy.show}</Button>
          <Button variant="secondary" disabled={busy} onClick={() => setConfirm(site.id)}>{copy.delete}</Button>
        </div>
        {expanded === site.id && <div>{pages === null ? <p role="status">{copy.loadPages}</p>
          : !pages.length ? <p>{copy.noPages}</p> : <ul className="list-inside list-disc">{pages.map(page =>
            <li key={page.id} className="break-all"><a href={page.url} target="_blank" rel="noopener noreferrer"
              className="underline">{page.url}</a></li>)}</ul>}</div>}
        {confirm === site.id && <div className="space-y-2 rounded border border-border p-3"><p>{copy.confirm}</p>
          <p className="break-all font-medium">{site.url}</p><div className="flex flex-wrap gap-2">
            <Button variant="destructive" disabled={busy} onClick={() => void mutate(async () => {
              await websites.remove(workspaceId, site.id); setConfirm('');
              if (data.items.length === 1 && offset) setOffset(Math.max(0, offset - data.limit));
            })}>{copy.yes}</Button>
            <Button variant="secondary" onClick={() => setConfirm('')}>{copy.cancel}</Button>
          </div></div>}
      </li>)}</ul>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - data.limit))}>{copy.previous}</Button>
        <Button variant="secondary" disabled={offset + data.limit >= data.total} onClick={() => setOffset(offset + data.limit)}>{copy.more}</Button>
      </div>
    </>}
  </section>;
}
