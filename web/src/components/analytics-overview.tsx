'use client';
import { useCopy, useDisplayLocale } from '@/i18n/provider';

import { useEffect, useState } from 'react';
import { overview, type Filters, type Overview } from '@/api/analytics';
import { Card } from '@/components/ui/primitives';


const field = 'min-h-11 rounded-lg border border-border bg-surface px-3 py-2';


const columns = ['conversations', 'messages', 'bot_share', 'handoff_rate', 'first_response_seconds', 'leads'] as const;


export function AnalyticsOverview({ workspaceId }: { workspaceId: string }) {
  const displayLocale = useDisplayLocale();
  const number = new Intl.NumberFormat(displayLocale, { maximumFractionDigits: 1 });
  const date = new Intl.DateTimeFormat(displayLocale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' });
  const copy = useCopy('analytics');
  function value(row: Overview['summary'], key: typeof columns[number]) {
    const amount = row[key];
    if (amount === null) return copy.unavailable;
    if (key === 'first_response_seconds') return copy.seconds.replace('{value}', number.format(amount));
    return number.format(amount) + (key === 'bot_share' || key === 'handoff_rate' ? '%' : '');
  }
  const [draft, setDraft] = useState<Filters>(() => ({
    from_date: new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10),
    to_date: new Date().toISOString().slice(0, 10), channel: '',
  }));
  const [filters, setFilters] = useState(draft);
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState<Overview>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    overview(filters, workspaceId, controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); setError(false); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [filters, workspaceId, retry]);
  function refresh() { setData(undefined); setError(false); setRetry(count => count + 1); }
  return <div className="mt-6 space-y-6">
    <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault(); setData(undefined); setError(false); setFilters({ ...draft });
    }}>
      {(['from_date', 'to_date'] as const).map(key => <label key={key} className="grid gap-1">
        <span>{key === 'from_date' ? copy.from : copy.to}</span>
        <input className={field + ' w-44 max-w-full'} required type="date" value={draft[key]}
          onChange={event => setDraft(previous => ({ ...previous, [key]: event.target.value }))} />
      </label>)}
      <label className="grid gap-1"><span>{copy.channel}</span>
        <select className={field} value={draft.channel} onChange={event =>
          setDraft(previous => ({ ...previous, channel: event.target.value as Filters['channel'] }))}>
          <option value="">{copy.all}</option><option value="web">{copy.web}</option><option value="whatsapp">{copy.whatsapp}</option>
        </select>
      </label>
      <button className={field}>{copy.apply}</button><button type="button" className={field} onClick={refresh}>{copy.refresh}</button>
    </form>
    {error ? <Card role="alert"><p>{copy.error}</p><button className={field} onClick={refresh}>{copy.retry}</button></Card>
      : !data ? <p role="status">{copy.loading}</p> : <>
        {data.updated_at && <p className="text-sm text-muted">{copy.updated.replace('{time}', date.format(new Date(data.updated_at)))}</p>}
        {!data.daily.length && <Card role="status">{copy.empty}</Card>}
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {columns.map(key => <Card key={key}><dt className="text-sm text-muted">{copy[key]}</dt>
            <dd className="mt-2 text-2xl font-semibold">{value(data.summary, key)}</dd></Card>)}
        </dl>
        <p className="text-sm text-muted">{copy.definitions}</p>
        {!!data.daily.length && <section className="space-y-3">
          <h2 className="text-xl font-semibold">{copy.daily}</h2>
          <div className="overflow-x-auto rounded-xl border border-border" tabIndex={0} role="region" aria-label={copy.daily}>
            <table className="w-full whitespace-nowrap text-left text-sm"><caption className="sr-only">{copy.daily}</caption>
              <thead className="bg-surface"><tr>{[copy.date, copy.channel, ...columns.map(key => copy[key])].map(label =>
                <th key={label} scope="col" className="p-3">{label}</th>)}</tr></thead>
              <tbody>{data.daily.map(row => <tr key={row.day + row.channel} className="border-t border-border">
                <th scope="row" className="p-3 font-normal">{row.day}</th>
                <td className="p-3">{row.channel === 'web' ? copy.web : copy.whatsapp}</td>
                {columns.map(key => <td key={key} className="p-3">{value(row, key)}</td>)}
              </tr>)}</tbody>
            </table>
          </div>
        </section>}
        <section aria-label={copy.milestones} className="space-y-3">
          <h2 className="text-xl font-semibold">{copy.milestones}</h2><p className="text-sm text-muted">{copy.milestoneHelp}</p>
          {Object.values(data.milestones).some(count => count > 0) ? <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(copy.events).filter(([key]) => data.milestones[key] > 0).map(([key, label]) =>
              <Card key={key}><dt>{label}</dt><dd className="mt-2 font-semibold">{number.format(data.milestones[key])}</dd></Card>)}
          </dl> : <p>{copy.noMilestones}</p>}
        </section>
      </>}
  </div>;
}
