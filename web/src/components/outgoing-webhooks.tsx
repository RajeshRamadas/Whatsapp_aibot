'use client';
import { useEffect, useState } from 'react';
import { webhookClient, eventTypes, type Endpoint, type EventType } from '@/api/outgoing-webhooks';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
import { WebhookHistory } from './webhook-history';

export function OutgoingWebhooks({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('webhooks'), locale = useDisplayLocale();
  const [rows, setRows] = useState<Endpoint[]>(), [loadingError, setLoadingError] = useState(false), [version, refresh] = useState(0);
  const [name, setName] = useState(''), [url, setUrl] = useState(''), [events, setEvents] = useState<EventType[]>(['lead.created']);
  const [secret, setSecret] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const controller = new AbortController(); setLoadingError(false);
    webhookClient.list(controller.signal).then(setRows).catch(() => { if (!controller.signal.aborted) setLoadingError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError(false);
    try { await action(); refresh(v => v + 1); } catch { setError(true); } finally { setBusy(false); }
  }
  const date = (value: string | null) => value ? new Date(value).toLocaleString(locale, { timeZone: 'Asia/Calcutta' }) : copy.never;
  return <section className="mt-8 min-w-0 space-y-5" aria-label={copy.title}>
    <h2 className="text-xl font-semibold">{copy.title}</h2><p>{copy.intro}</p><p className="text-sm text-muted">{copy.policy}</p>
    {error && <p role="alert">{copy.actionError}</p>}
    {secret && <section className="space-y-3 rounded-xl border border-border p-4" aria-label={copy.secretTitle}>
      <h3 className="font-semibold">{copy.secretTitle}</h3><p>{copy.secretHelp}</p><label htmlFor="webhook-secret">{copy.secretLabel}</label>
      <textarea id="webhook-secret" className={field + ' font-mono'} value={secret} readOnly autoComplete="off" spellCheck={false} rows={3} />
      <Button variant="secondary" onClick={() => setSecret('')}>{copy.savedSecret}</Button>
    </section>}
    <details className="rounded-xl border border-border p-4"><summary className="min-h-11 cursor-pointer font-semibold">{copy.add}</summary>
      <form aria-label={copy.add} className="mt-3 space-y-3" onSubmit={async e => {
        e.preventDefault(); setSecret(''); await act(async () => { const result = await webhookClient.create(name.trim(), url.trim(), events); setSecret(result.secret); setName(''); setUrl(''); });
      }}><div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1">{copy.name}<input className={field} required maxLength={100} value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
        <label className="grid gap-1">{copy.url}<input className={field} type="url" required maxLength={2048} value={url} disabled={busy} onChange={e => setUrl(e.target.value)} autoComplete="off" /></label></div>
      <p className="text-sm text-muted">{copy.urlHelp}</p><fieldset disabled={busy}><legend className="font-medium">{copy.eventsTitle}</legend><div className="grid gap-1 sm:grid-cols-2">{eventTypes.map(event => <label key={event} className="flex min-h-11 items-center gap-3">
        <input type="checkbox" checked={events.includes(event)} onChange={e => setEvents(values => e.target.checked ? [...values, event] : values.filter(v => v !== event))} />{copy.events[event]}</label>)}</div></fieldset>
      <Button disabled={busy || !name.trim() || !url.trim() || !events.length}>{copy.create}</Button></form>
    </details>
    <Button variant="secondary" disabled={busy} onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!rows && !loadingError && <p role="status">{copy.loading}</p>}{loadingError && <p role="alert">{copy.loadError}</p>}
    {rows?.length === 0 && <p>{copy.empty}</p>}
    {rows?.map(row => <section key={row.id} className="min-w-0 space-y-3 rounded-xl border border-border p-4" aria-label={row.name}>
      <h3 className="break-words text-lg font-semibold">{row.name}</h3><p className="break-all font-mono text-sm">{row.url}</p>
      <p>{row.enabled ? copy.enabled : row.disabled_reason === 'continuous_failure' ? copy.disabled : copy.paused}</p>
      <p className="text-sm">{copy.secretHint}: …{row.secret_last4} · {copy.lastSuccess}: {date(row.last_success_at)}</p>
      {row.last_error && <p role="status">{copy.failures[row.last_error as keyof typeof copy.failures] ?? copy.failure}</p>}
      <ul className="flex flex-wrap gap-2 text-sm">{row.events.map(event => <li key={event} className="rounded bg-border px-2 py-1">{copy.events[event]}</li>)}</ul>
      {!row.enabled && <label className="flex min-h-11 items-start gap-3"><input className="mt-1" type="checkbox" checked={acknowledged[row.id] ?? false} disabled={busy} onChange={e => setAcknowledged(v => ({ ...v, [row.id]: e.target.checked }))} /><span>{copy.consent}</span></label>}
      <div className="flex flex-wrap gap-3"><Button disabled={busy || !row.enabled && !acknowledged[row.id]} onClick={() => void act(() => webhookClient.enable(row.id, !row.enabled))}>{row.enabled ? copy.pause : copy.enable}</Button>
        <Button variant="destructive" disabled={busy} onClick={() => { if (window.confirm(copy.deletePrompt)) { setSecret(''); void act(() => webhookClient.remove(row.id)); } }}>{copy.remove}</Button></div>
      <WebhookHistory endpoint={row} revision={version} onResent={() => refresh(v => v + 1)} />
    </section>)}
    <details className="rounded-xl border border-border p-4"><summary className="min-h-11 cursor-pointer font-semibold">{copy.advanced}</summary><p>{copy.signatureHelp}</p><p className="mt-3">{copy.deliveryHelp}</p>
      <p className="mt-3 font-mono text-sm">X-Signature: t=&lt;unix time&gt;,v1=&lt;HMAC-SHA256&gt;</p></details>
  </section>;
}
