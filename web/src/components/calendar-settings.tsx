'use client';
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { calendarClient, type CalendarData } from '@/api/calendar';
import type { Catalog } from '@/api/appointments';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from '@/components/ui/button';
import { field } from './appointment-settings';
export function CalendarSettings({ workspaceId, catalog }: { workspaceId: string; catalog: Catalog }) {
  const copy = useCopy('appointments').calendar, locale = useDisplayLocale();
  const api = useMemo(() => calendarClient(workspaceId), [workspaceId]);
  const [data, setData] = useState<CalendarData>(), [error, setError] = useState(false);
  const [busy, setBusy] = useState(false), [revision, refresh] = useState(0);
  const [label, setLabel] = useState(''), [selection, setSelection] = useState('');
  const [service, setService] = useState(''), [staff, setStaff] = useState(''), [consent, setConsent] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.load(controller.signal).then(value => { setData(value); setError(false); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    const timer = setTimeout(() => refresh(v => v + 1), 10000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, revision]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError(false);
    try { await operation(); refresh(v => v + 1); }
    catch { setError(true); }
    finally { setBusy(false); }
  }
  const status = (value: string) => (copy.states as Record<string, string>)[value] ?? copy.attention;
  return <details className="rounded-xl border border-border p-4">
    <summary className="min-h-11 cursor-pointer py-2 font-semibold"><h2 className="inline">{copy.title}</h2></summary>
    <div className="mt-4 min-w-0 space-y-4">
      <p>{copy.help}</p><p className="text-sm text-muted">{copy.permissions}</p>
      <Link className="inline-block min-h-11 underline" href="/dashboard/bot">{copy.hours}</Link>
      {!data && !error && <p role="status">{copy.loading}</p>}
      {error && <p role="alert">{copy.error} <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></p>}
      {data && <>
        {!data.accounts.configured && <p role="status">{copy.disabled}</p>}
        <form aria-label={copy.connect} className="grid min-w-0 gap-3 sm:grid-cols-[1fr_auto]" onSubmit={event => {
          event.preventDefault(); void act(async () => { const result = await api.connect(label); window.location.assign(result.url); });
        }}>
          <label className="grid min-w-0 gap-1">{copy.label}<input className={field} value={label} required maxLength={100}
            disabled={busy || !data.accounts.configured} onChange={e => setLabel(e.target.value)} /></label>
          <Button className="self-end" disabled={busy || !data.accounts.configured}>{copy.connect}</Button>
        </form>
        {data.accounts.connections.length === 0 && <p>{copy.empty}</p>}
        <ul className="space-y-3">{data.accounts.connections.map(c => <li key={c.id} className="min-w-0 space-y-2 break-words rounded-lg border border-border p-3">
          <h3 className="font-semibold">{c.label} · {status(c.status)}</h3>
          <p>{copy.success}: {c.last_success_at ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: catalog.timezone }).format(new Date(c.last_success_at)) : copy.never}</p>
          {c.last_error && <p role="status">{copy.attention}</p>}
          <ul>{c.calendars.map(calendar => <li key={calendar.id}>{calendar.name}</li>)}</ul>
          <div className="flex flex-wrap gap-2">
            {['connected', 'needs_attention'].includes(c.status) && <Button variant="secondary" disabled={busy} onClick={() => void act(() => api.refresh(c.id))}>{copy.test}</Button>}
            {c.status !== 'disconnected' && <Button variant="secondary" disabled={busy} onClick={() => {
              if (window.confirm(copy.disconnectConfirm)) void act(() => api.disconnect(c.id));
            }}>{copy.disconnect}</Button>}
          </div>
        </li>)}</ul>
        <form aria-label={copy.binding} className="grid min-w-0 grid-cols-1 gap-3 rounded-lg border border-border p-3 sm:grid-cols-2" onSubmit={event => {
          event.preventDefault(); void act(async () => {
            await api.bind({ connection_id: selection.slice(0, 36), calendar_id: selection.slice(37),
              service_id: service || null, staff_id: staff || null, confirm_data_sharing: consent });
            setConsent(false);
          });
        }}>
          <h3 className="font-semibold sm:col-span-2">{copy.binding}</h3>
          <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.calendar}<select className={field} required value={selection} disabled={busy}
            onChange={e => setSelection(e.target.value)}><option value="">{copy.choose}</option>
            {data.accounts.connections.filter(c => c.status === 'connected').flatMap(c => c.calendars.map(calendar =>
              <option key={c.id + calendar.id} value={c.id + ':' + calendar.id}>{c.label} · {calendar.name}</option>))}</select></label>
          <label className="grid min-w-0 gap-1">{copy.service}<select className={field} value={service} disabled={busy} onChange={e => setService(e.target.value)}>
            <option value="">{copy.allServices}</option>{catalog.services.filter(s => s.active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="grid min-w-0 gap-1">{copy.staff}<select className={field} value={staff} disabled={busy} onChange={e => setStaff(e.target.value)}>
            <option value="">{copy.allStaff}</option>{catalog.staff.filter(s => s.active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <p className="text-sm text-muted sm:col-span-2">{copy.bindingHelp}</p>
          <label className="flex min-h-11 items-start gap-2 sm:col-span-2"><input className="mt-1" type="checkbox" required checked={consent}
            disabled={busy} onChange={e => setConsent(e.target.checked)} />{copy.consent}</label>
          <Button disabled={busy || !consent || (!service && !staff)}>{copy.link}</Button>
        </form>
        {data.bindings.length === 0 && <p>{copy.noBindings}</p>}
        <ul className="space-y-3">{data.bindings.map(b => <li key={b.id} className="min-w-0 break-words rounded-lg border border-border p-3">
          <p>{data.accounts.connections.find(c => c.id === b.connection_id)?.calendars.find(c => c.id === b.calendar_id)?.name ?? copy.calendar}
            {' · '}{catalog.services.find(s => s.id === b.service_id)?.name ?? copy.allServices}
            {' · '}{catalog.staff.find(s => s.id === b.staff_id)?.name ?? copy.allStaff}</p>
          <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.unlinkConfirm)) void act(() => api.unbind(b.id)); }}>{copy.unlink}</Button>
        </li>)}</ul>
      </>}
    </div>
  </details>;
}
