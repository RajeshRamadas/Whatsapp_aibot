'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { components } from '@/api/schema';
import type { Act, SyncClient, SyncConnection } from '@/api/lead-sync';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { Badge } from './ui/primitives';
import { field } from './appointment-settings';
export function LeadSyncConnection({ connection: c, api, act, busy, revision }: {
  connection: SyncConnection; api: SyncClient; act: Act; busy: boolean; revision: number;
}) {
  const copy = useCopy('leadSync'), leads = useCopy('leads'), locale = useDisplayLocale();
  const [enabled, setEnabled] = useState(c.enabled), [consent, setConsent] = useState(false);
  const [notes, setNotes] = useState(c.config.include_notes ?? false), [mapping, setMapping] = useState(c.config.mapping ?? {});
  const [file, setFile] = useState(c.config.spreadsheet_id ?? ''), [tab, setTab] = useState(String(c.config.tab_id ?? ''));
  const [jobs, setJobs] = useState<components['schemas']['SyncJob'][]>(), [jobError, setJobError] = useState(false);
  const files = c.catalog.files ?? [], fields = c.catalog.fields ?? [];
  const state = (value: string) => (copy.states as Record<string, string>)[value] ?? copy.states.failed;
  const source = (value: string) => ({ ...leads.fields, ...copy.sources } as Record<string, string>)[value] ?? value;
  useEffect(() => {
    const controller = new AbortController();
    api.jobs(c.id, controller.signal).then(v => { if (!controller.signal.aborted) { setJobs(v); setJobError(false); } })
      .catch(() => { if (!controller.signal.aborted) setJobError(true); });
    return () => controller.abort();
  }, [api, c.id, revision]);
  return <section aria-label={c.label} className="min-w-0 space-y-3 break-words rounded-xl border border-border p-4">
    <h2 className="text-xl font-semibold">{c.label}</h2><div className="flex flex-wrap gap-2"><Badge>{c.provider === 'sheets' ? 'Google Sheets' : c.provider === 'hubspot' ? 'HubSpot' : 'Zoho CRM'}</Badge><Badge>{state(c.status)}</Badge><Badge>{c.enabled ? copy.sharingOn : copy.sharingOff}</Badge></div>
    {c.key_last4 && <p className="text-sm text-muted">{copy.credentialTail.replace('{last4}', c.key_last4)}</p>}
    {c.last_error && <p role="status">{(copy.failures as Record<string, string>)[c.last_error] ?? copy.operationError}</p>}
    {c.last_success_at && <p className="text-sm text-muted">{copy.lastSuccess}: {new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(c.last_success_at))}</p>}
    {c.status !== 'disconnected' && <details><summary className="min-h-11 cursor-pointer font-medium">{copy.settings}</summary>
      <div className="mb-4 flex flex-wrap gap-2"><Button variant="secondary" disabled={busy || c.status === 'requested' || c.status === 'connecting'} onClick={() => void act(() => api.discover(c.id))}>{copy.test}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.disconnectPrompt)) void act(() => api.disconnect(c.id)); }}>{copy.disconnect}</Button></div>
      {c.provider === 'sheets' && <form aria-label={copy.create} className="mb-4 grid gap-2" onSubmit={e => {
        e.preventDefault(); const title = new FormData(e.currentTarget).get('title') as string; void act(() => api.create(c.id, title));
      }}><label className="grid gap-1">{copy.sheetTitle}<input className={field} name="title" required maxLength={100} defaultValue="Leads" /></label><Button disabled={busy || c.status !== 'connected'}>{copy.create}</Button></form>}
      <form aria-label={copy.settings} className="grid min-w-0 gap-3" onSubmit={async e => {
        e.preventDefault(); if (await act(() => api.save(c.id, { enabled, confirm_sharing: consent, include_notes: notes, mapping,
          spreadsheet_id: file || null, tab_id: tab === '' ? null : Number(tab) }))) setConsent(false);
      }}>
        {c.provider === 'sheets' ? <>
          <label className="grid gap-1">{copy.file}<select className={field} value={file} disabled={busy} onChange={e => { setFile(e.target.value); setTab(''); if (e.target.value) void act(() => api.discover(c.id, e.target.value)); }}>
            <option value="">{copy.choose}</option>{files.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
          <label className="grid gap-1">{copy.tab}<select className={field} value={tab} disabled={busy || !file} onChange={e => setTab(e.target.value)}>
            <option value="">{copy.choose}</option>{files.find(f => f.id === file)?.tabs?.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
          <p className="text-sm text-muted">{copy.sheetHelp}</p>
        </> : <><p className="text-sm text-muted">{copy.mappingHelp}</p><div className="grid gap-3 sm:grid-cols-2">{(['name', 'phone', 'email', 'need', 'budget', 'location', 'preferred_time', 'status', 'conversation_url', 'updated_at'] as const).map(key =>
          <label className="grid min-w-0 gap-1" key={key}>{source(key)}<select className={field} disabled={busy} value={mapping[key] ?? ''} onChange={e => setMapping(v => { const next = { ...v }; if (e.target.value) next[key] = e.target.value; else delete next[key]; return next; })}>
            <option value="">{copy.noMapping}</option>{fields.map(f => <option key={f.name} value={f.name}>{f.label}{f.required ? ' *' : ''}</option>)}</select></label>)}</div></>}
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={notes} disabled={busy} onChange={e => setNotes(e.target.checked)} />{copy.notes}</label>
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={enabled} disabled={busy} onChange={e => setEnabled(e.target.checked)} />{copy.enable}</label>
        {enabled && <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={consent} required disabled={busy} onChange={e => setConsent(e.target.checked)} />{copy.consent}</label>}
        <Button disabled={busy || enabled && (c.status !== 'connected' || !consent)}>{copy.save}</Button>
      </form>
      <Button className="mt-4" variant="secondary" disabled={busy || !c.enabled} onClick={() => void act(() => api.backfill(c.id, count => window.confirm(copy.backfillPrompt.replace('{count}', String(count)))))}>{copy.backfill}</Button>
    </details>}
    <details><summary className="min-h-11 cursor-pointer font-medium">{copy.jobs}</summary><p className="mb-3 text-sm text-muted">{copy.jobHelp}</p>
      {jobError && <p role="alert">{copy.loadError}</p>}{!jobs && !jobError && <p role="status">{copy.loading}</p>}
      {jobs?.length === 0 && <p>{copy.noJobs}</p>}<ul className="space-y-3">{jobs?.map(j => <li key={j.id} className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border pt-3">
        <span>{j.kind === 'sync' ? copy.version.replace('{version}', String(j.lead_version)) : copy.setupJob} · {state(j.state)}</span>
        {j.last_error && <span className="w-full text-sm">{(copy.failures as Record<string, string>)[j.last_error] ?? copy.operationError}</span>}
        {j.conversation_id && <Link className="inline-block min-h-11 underline" href={'/dashboard/inbox?conversation=' + j.conversation_id}>{copy.conversation}</Link>}
        {j.state === 'failed' && c.status !== 'disconnected' && <Button variant="secondary" disabled={busy} onClick={() => void act(() => api.retry(c.id, j.id))}>{copy.retryJob}</Button>}
      </li>)}</ul>
    </details>
  </section>;
}
