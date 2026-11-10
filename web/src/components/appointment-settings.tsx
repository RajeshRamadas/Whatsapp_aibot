'use client';
import { useState } from 'react';
import { useCopy } from '@/i18n/provider';
import type { Action, Catalog, Client, Template } from '@/api/appointments';
import { Button } from '@/components/ui/button';
export const field = 'min-h-11 min-w-0 w-full rounded-lg border border-border bg-surface p-2';
export function AppointmentSettings({ catalog, templates, api, act, busy }: {
  catalog: Catalog; templates: Template[]; api: Client; act: Action; busy: boolean;
}) {
  const copy = useCopy('appointments');
  const [serviceId, setServiceId] = useState(''), [staffId, setStaffId] = useState('');
  const [name, setName] = useState(''), [duration, setDuration] = useState(30), [lead, setLead] = useState(60);
  const [template, setTemplate] = useState(''), [active, setActive] = useState(true);
  const [staffName, setStaffName] = useState(''), [staffActive, setStaffActive] = useState(true);
  function chooseService(id: string) {
    setServiceId(id);
    const value = catalog.services.find(item => item.id === id);
    setName(value?.name ?? ''); setDuration(value?.duration_minutes ?? 30); setLead(value?.reminder_minutes ?? 60);
    setTemplate(value?.reminder_template_id ?? ''); setActive(value?.active ?? true);
  }
  function chooseStaff(id: string) {
    setStaffId(id);
    const value = catalog.staff.find(item => item.id === id);
    setStaffName(value?.name ?? ''); setStaffActive(value?.active ?? true);
  }
  const utility = templates.filter(item => item.category === 'UTILITY' && item.body
    && new Set(item.body.match(/\{\{\d+\}\}/g)).size === 3);
  return <details className="rounded-xl border border-border p-4">
    <summary className="min-h-11 cursor-pointer py-2 font-semibold">{copy.settings}</summary>
    <div className="mt-4 grid gap-6 lg:grid-cols-2">
      <form aria-label={copy.service} className="grid min-w-0 grid-cols-1 gap-3" onSubmit={async event => {
        event.preventDefault();
        const ok = await act(() => api.service({ name, duration_minutes: duration, reminder_minutes: lead,
          reminder_template_id: template || null, active }, serviceId || undefined));
        if (ok && !serviceId) chooseService('');
      }}>
        <h2 className="font-semibold">{copy.service}</h2>
        <label className="grid min-w-0 gap-1">{copy.service}<select className={field} value={serviceId} disabled={busy}
          onChange={e => chooseService(e.target.value)}><option value="">{copy.newService}</option>
          {catalog.services.map(s => <option key={s.id} value={s.id}>{s.name}{s.active ? '' : ' · ' + copy.inactive}</option>)}</select></label>
        <label className="grid gap-1">{copy.name}<input className={field} required maxLength={100} disabled={busy}
          value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="grid gap-1">{copy.duration}<input className={field} type="number" min={5} max={480} required
          disabled={busy} value={duration} onChange={e => setDuration(Number(e.target.value))} /></label>
        <label className="grid gap-1">{copy.leadTime}<input className={field} type="number" min={5} max={10080} required
          disabled={busy} value={lead} onChange={e => setLead(Number(e.target.value))} /></label>
        <label className="grid gap-1">{copy.template}<select className={field} disabled={busy} value={template}
          onChange={e => setTemplate(e.target.value)}><option value="">{copy.noTemplate}</option>
          {utility.map(t => <option key={t.id} value={t.id}>{t.name} · {t.language}</option>)}</select></label>
        <p className="text-sm text-muted">{copy.templateHelp}</p>
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" disabled={busy} checked={active}
          onChange={e => setActive(e.target.checked)} />{copy.active}</label>
        <Button disabled={busy}>{copy.saveService}</Button>
      </form>
      <form aria-label={copy.staff} className="grid min-w-0 grid-cols-1 content-start gap-3" onSubmit={async event => {
        event.preventDefault();
        if (await act(() => api.staff({ name: staffName, active: staffActive }, staffId || undefined)) && !staffId) chooseStaff('');
      }}>
        <h2 className="font-semibold">{copy.staff}</h2>
        <label className="grid gap-1">{copy.staff}<select className={field} value={staffId} disabled={busy}
          onChange={e => chooseStaff(e.target.value)}><option value="">{copy.newStaff}</option>
          {catalog.staff.map(s => <option key={s.id} value={s.id}>{s.name}{s.active ? '' : ' · ' + copy.inactive}</option>)}</select></label>
        <label className="grid gap-1">{copy.name}<input className={field} required maxLength={100} disabled={busy}
          value={staffName} onChange={e => setStaffName(e.target.value)} /></label>
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" disabled={busy} checked={staffActive}
          onChange={e => setStaffActive(e.target.checked)} />{copy.active}</label>
        <Button disabled={busy}>{copy.saveStaff}</Button>
      </form>
    </div>
  </details>;
}
