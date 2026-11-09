'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import * as platform from '@/api/platform';
import { Badge, Card } from '@/components/ui/primitives';
import copy from '@/messages/platform.en.json';

const button = 'min-h-11 rounded-lg border border-border px-3 py-2 disabled:opacity-50';
const number = new Intl.NumberFormat('en-IN');
const usd = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 });
const date = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' });
type Page<T> = { items: T[]; total: number; limit: number; offset: number };

function Records<T extends { id: string }>({ title, help, load, render, revision = 0 }: {
  title: string; help?: string; load: (offset: number, signal: AbortSignal) => Promise<Page<T>>;
  render: (item: T) => React.ReactNode; revision?: number;
}) {
  const [data, setData] = useState<Page<T>>();
  const [offset, setOffset] = useState(0);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    load(offset, controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); setError(false); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [load, offset, retry, revision]);
  function refresh() { setData(undefined); setError(false); setRetry(value => value + 1); }
  function move(value: number) { setData(undefined); setError(false); setOffset(value); }
  return <section aria-label={title} className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-xl font-semibold">{title}</h2>
      <button className={button} onClick={refresh}>{copy.refresh}</button>
    </div>
    {help && <p className="text-sm text-muted">{help}</p>}
    {error ? <div role="alert"><p>{copy.error}</p><button className={button} onClick={refresh}>{copy.retry}</button></div>
      : !data ? <p role="status">{copy.loading}</p> : <>
        {!data.items.length ? <Card>{copy.empty}</Card> : <ul className="grid gap-3">
          {data.items.map(item => <li key={item.id}><Card className="space-y-3 break-words">{render(item)}</Card></li>)}
        </ul>}
        <div className="flex flex-wrap items-center gap-3">
          <button className={button} disabled={offset === 0} onClick={() => move(Math.max(0, offset - data.limit))}>{copy.previous}</button>
          <p aria-live="polite" className="text-sm">{copy.page.replace('{from}', String(data.total ? Math.min(offset + 1, data.total) : 0))
            .replace('{to}', String(Math.min(offset + data.items.length, data.total))).replace('{total}', String(data.total))}</p>
          <button className={button} disabled={offset + data.limit >= data.total} onClick={() => move(offset + data.limit)}>{copy.next}</button>
        </div>
      </>}
  </section>;
}

export function PlatformConsole() {
  const router = useRouter();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);
  async function toggle(tenant: platform.Tenant) {
    if (busy || !window.confirm((tenant.suspended ? copy.confirmResume : copy.confirmSuspend).replace('{name}', tenant.name))) return;
    setBusy(tenant.id); setError(false); setSaved(false);
    try {
      await platform.suspend(tenant.id, !tenant.suspended);
      setRevision(value => value + 1); setSaved(true);
    } catch { setError(true); } finally { setBusy(undefined); }
  }
  async function logout() {
    setBusy('logout'); setError(false);
    try { await platform.logout(); router.replace('/login'); router.refresh(); }
    catch { setError(true); } finally { setBusy(undefined); }
  }
  return <div className="space-y-10">
    <button className={button} disabled={!!busy} onClick={() => void logout()}>{copy.logout}</button>
    {error && <p role="alert">{copy.saveError}</p>}
    {saved && <p role="status">{copy.saved}</p>}
    <Records title={copy.tenants} load={platform.tenants} revision={revision} render={tenant => <>
      <h3 className="text-lg font-semibold">{tenant.name}</h3>
      <div className="flex flex-wrap gap-2"><Badge tone={tenant.suspended ? 'danger' : 'success'}>
        {tenant.suspended ? copy.suspended : copy.active}</Badge>
        <Badge>{tenant.bot_live ? copy.live : copy.paused}</Badge></div>
      <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
        {[[copy.plan, tenant.plan], [copy.messages, number.format(tenant.ai_messages)],
          [copy.tokens, `${number.format(tenant.input_tokens)} / ${number.format(tenant.output_tokens)}`],
          [copy.cost, usd.format(Number(tenant.cost_usd))], [copy.workspaceId, tenant.id]].map(([label, value]) =>
          <div key={label}><dt className="text-muted">{label}</dt><dd>{value}</dd></div>)}
      </dl>
      {tenant.unpriced_calls > 0 && <p className="text-sm">{copy.unpriced.replace('{count}', number.format(tenant.unpriced_calls))}</p>}
      <button className={button} disabled={!!busy} onClick={() => void toggle(tenant)}>
        {busy === tenant.id ? copy.saving : tenant.suspended ? copy.resume : copy.suspend}</button>
    </>} />
    <Records title={copy.jobs} help={copy.jobHelp} load={platform.failedJobs} render={job => <>
      <h3 className="font-semibold">{copy[job.kind]}</h3>
      <p>{job.tenant_id ? `${copy.workspaceId}: ${job.tenant_id}` : copy.accountEmail}</p><p>{copy.recordId}: {job.id}</p>
      <p>{copy.time}: {date.format(new Date(job.received_at))}</p>
    </>} />
    <Records title={copy.webhooks} help={copy.webhookHelp} load={platform.webhookFailures} render={failure => <>
      <h3 className="font-semibold">{failure.provider === 'whatsapp' ? copy.whatsapp : failure.provider === 'ses' ? copy.email : copy.razorpay}</h3>
      <p>{copy.httpStatus}: {failure.http_status}</p><p>{copy.time}: {date.format(new Date(failure.received_at))}</p>
    </>} />
  </div>;
}
