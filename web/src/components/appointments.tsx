'use client';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { client, type Booking, type Language, type Slot } from '@/api/appointments';
import { AppointmentSettings, field } from '@/components/appointment-settings';
import { Button } from '@/components/ui/button';
import { Badge, Card } from '@/components/ui/primitives';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
const day = (value: Date) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
export function Appointments({ workspaceId, admin }: { workspaceId: string; admin: boolean }) {
  const copy = useCopy('appointments'), locale = useDisplayLocale();
  const api = useMemo(() => client(workspaceId), [workspaceId]);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.load>>>();
  const [from, setFrom] = useState(''), [to, setTo] = useState(''), [zone, setZone] = useState('');
  const [revision, refresh] = useState(0), [busy, setBusy] = useState(false);
  const [error, setError] = useState(false), [loadError, setLoadError] = useState(false), [saved, setSaved] = useState(false);
  const [serviceId, setServiceId] = useState(''), [staffId, setStaffId] = useState(''), [starts, setStarts] = useState('');
  const [slotId, setSlotId] = useState(''), [conversationId, setConversationId] = useState('');
  const [name, setName] = useState(''), [phone, setPhone] = useState(''), [consent, setConsent] = useState(false);
  const [replacement, setReplacement] = useState('');
  const [language, setLanguage] = useState<Language | ''>('');
  const composer = useRef<HTMLSelectElement>(null);
  const [catalog, slots, bookings, conversations, templates] = data ?? [];
  const date = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: catalog?.timezone || 'UTC' });
  const time = (value: string) => date.format(new Date(value));
  const status = (value: string) => (copy.states as Record<string, string>)[value] ?? value;
  useEffect(() => {
    const start = new Date(), end = new Date(); end.setDate(end.getDate() + 29);
    setFrom(day(start)); setTo(day(end)); setZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
  }, []);
  useEffect(() => {
    if (!from || !to) return;
    const controller = new AbortController();
    const end = new Date(to + 'T00:00:00'); end.setDate(end.getDate() + 1);
    const query = new URLSearchParams({ start: new Date(from + 'T00:00:00').toISOString(), end: end.toISOString() });
    api.load(query, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setLoadError(false); }
    }).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [api, from, to, revision]);
  useEffect(() => {
    const source = new EventSource('/api/backend/events');
    const changed = () => refresh(value => value + 1);
    source.addEventListener('appointments.updated', changed);
    const timer = setInterval(changed, 10000);
    return () => { source.close(); clearInterval(timer); };
  }, [workspaceId]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError(false); setSaved(false);
    try { await operation(); setSaved(true); refresh(value => value + 1); return true; }
    catch { setError(true); return false; }
    finally { setBusy(false); }
  }
  function reschedule(booking: Booking) {
    setReplacement(booking.id); setConversationId(booking.conversation_id);
    setLanguage(booking.language ?? '');
    setName(booking.customer_name); setPhone(booking.customer_phone || ''); setConsent(booking.reminder_opt_in ?? false);
    composer.current?.focus();
  }
  function reserve(slot: Slot) { setSlotId(slot.id); composer.current?.focus(); }
  return <div className="mt-5 min-w-0 space-y-5">
    <p className="text-sm text-muted">{copy.zone.replace('{device}', zone).replace('{workspace}', catalog?.timezone || '')}</p>
    <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
      <label className="grid gap-1">{copy.from}<input className={field} type="date" required value={from} max={to}
        onChange={e => setFrom(e.target.value)} /></label>
      <label className="grid gap-1">{copy.to}<input className={field} type="date" required value={to} min={from}
        onChange={e => setTo(e.target.value)} /></label>
      <Button variant="secondary" onClick={() => refresh(value => value + 1)}>{copy.refresh}</Button>
    </div>
    <p className="text-sm text-muted">{copy.limit}</p>
    {!data && !loadError && <p role="status">{copy.loading}</p>}
    {loadError && <div role="alert">{copy.loadError} <Button onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></div>}
    {error && <p role="alert">{copy.error}</p>}{saved && <p role="status">{copy.saved}</p>}
    {catalog && <>
      {admin && <AppointmentSettings catalog={catalog} templates={templates ?? []} api={api} act={act} busy={busy} />}
      {admin && <form aria-label={copy.addSlot} className="grid grid-cols-1 gap-3 rounded-xl border border-border p-4 sm:grid-cols-2" onSubmit={async event => {
        event.preventDefault();
        if (await act(() => api.slot({ service_id: serviceId, staff_id: staffId, starts_at: new Date(starts).toISOString() }))) setStarts('');
      }}>
        <h2 className="font-semibold sm:col-span-2">{copy.addSlot}</h2>
        <label className="grid min-w-0 gap-1">{copy.service}<select className={field} required disabled={busy} value={serviceId}
          onChange={e => setServiceId(e.target.value)}><option value="">{copy.choose}</option>
          {catalog.services.filter(s => s.active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className="grid min-w-0 gap-1">{copy.staff}<select className={field} required disabled={busy} value={staffId}
          onChange={e => setStaffId(e.target.value)}><option value="">{copy.choose}</option>
          {catalog.staff.filter(s => s.active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className="grid gap-1">{copy.starts}<input className={field} required type="datetime-local" disabled={busy}
          value={starts} onChange={e => setStarts(e.target.value)} /></label>
        <Button disabled={busy}>{copy.addSlot}</Button>
      </form>}
      <section aria-label={copy.availability} className="space-y-3"><h2 className="text-xl font-semibold">{copy.availability}</h2>
        {slots?.length === 0 && <p>{copy.emptySlots}</p>}
        <ul className="grid gap-3 lg:grid-cols-2">{slots?.map(slot => <li key={slot.id}><Card className="min-w-0 space-y-2 break-words">
          <h3 className="font-semibold">{slot.service} · {slot.staff}</h3><p>{time(slot.starts_at)} – {time(slot.ends_at)}</p>
          <Badge>{status(slot.state)}</Badge>
          {slot.state === 'available' && <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => reserve(slot)}>{copy.reserve}</Button>
            {admin && <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.removeConfirm)) void act(() => api.remove(slot.id)); }}>{copy.remove}</Button>}</div>}
        </Card></li>)}</ul>
      </section>
      <form aria-label={copy.bookingForm} className="grid grid-cols-1 gap-3 rounded-xl border border-border p-4 sm:grid-cols-2" onSubmit={async event => {
        event.preventDefault();
        if (await act(() => api.hold({ slot_id: slotId, conversation_id: conversationId, customer_name: name,
          customer_phone: phone, language: language || null, reminder_opt_in: consent, replaces_id: replacement || null }))) {
          setSlotId(''); setName(''); setPhone(''); setConsent(false); setReplacement(''); setLanguage('');
        }
      }}>
        <h2 className="font-semibold sm:col-span-2">{copy.bookingForm}</h2>
        <p className="text-sm text-muted sm:col-span-2">{copy.holdHelp}</p>
        {replacement && <p role="status" className="sm:col-span-2">{copy.rescheduleHelp} <button type="button" className="min-h-11 underline" onClick={() => setReplacement('')}>{copy.clearReplacement}</button></p>}
        <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.availability}<select ref={composer} className={field} required disabled={busy} value={slotId}
          onChange={e => setSlotId(e.target.value)}><option value="">{copy.choose}</option>
          {slots?.filter(s => s.state === 'available').map(s => <option key={s.id} value={s.id}>{s.service} · {s.staff} · {time(s.starts_at)}</option>)}</select></label>
        <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.conversation}<select className={field} required disabled={busy || !!replacement} value={conversationId}
          onChange={e => { setConversationId(e.target.value); setPhone(conversations?.find(c => c.id === e.target.value)?.channel === 'whatsapp' ? conversations?.find(c => c.id === e.target.value)?.user ?? '' : ''); }}>
          <option value="">{copy.choose}</option>{conversations?.map(c => <option key={c.id} value={c.id}>{c.user || c.channel} · {c.id.slice(0, 8)}</option>)}</select></label>
        <label className="grid gap-1">{copy.customer}<input className={field} required maxLength={100} disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="grid gap-1">{copy.phone}<input className={field} required type="tel" pattern="\+?[1-9][0-9]{6,14}" disabled={busy} value={phone} onChange={e => setPhone(e.target.value)} /></label>
        <label className="grid gap-1 sm:col-span-2">{copy.language}<select className={field} disabled={busy} value={language}
          onChange={e => setLanguage(e.target.value as Language | '')}><option value="">{copy.defaultLanguage}</option>
          {Object.entries(copy.languages).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>
        <label className="flex min-h-11 items-center gap-2 sm:col-span-2"><input type="checkbox" disabled={busy} checked={consent} onChange={e => setConsent(e.target.checked)} />{copy.consent}</label>
        <Button disabled={busy}>{copy.hold}</Button>
      </form>
      <section aria-label={copy.bookings} className="space-y-3"><h2 className="text-xl font-semibold">{copy.bookings}</h2>
        <p className="text-sm text-muted">{copy.reminderHelp}</p>{bookings?.length === 0 && <p>{copy.emptyBookings}</p>}
        <ul className="grid gap-3 lg:grid-cols-2">{bookings?.map(booking => <li key={booking.id}><Card className="min-w-0 space-y-3 break-words">
          <h3 className="font-semibold">{booking.service} · {booking.staff}</h3><p>{time(booking.starts_at)} – {time(booking.ends_at)}</p>
          <Badge>{status(booking.state)}</Badge>
          <details><summary className="min-h-11 cursor-pointer py-2">{copy.contact}</summary><p>{booking.customer_name} · {booking.customer_phone}</p>
            <p>{copy.language}: {booking.language ? copy.languages[booking.language] : copy.defaultLanguage}</p></details>
          <Link className="inline-block min-h-11 underline" href={'/dashboard/inbox?conversation=' + booking.conversation_id}>{copy.openConversation}</Link>
          {booking.state === 'held' && <><p>{copy.expires}: {time(booking.expires_at)}</p><Button disabled={busy} onClick={() => {
            if (window.confirm(copy.confirmPrompt.replace('{service}', booking.service).replace('{time}', time(booking.starts_at)))) void act(() => api.change(booking.id, 'confirm'));
          }}>{copy.confirm}</Button></>}
          {['held', 'confirmed'].includes(booking.state) && <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => {
            if (window.confirm(copy.cancelPrompt)) void act(() => api.change(booking.id, 'cancel'));
          }}>{copy.cancel}</Button>{booking.state === 'confirmed' && <Button variant="secondary" disabled={busy} onClick={() => reschedule(booking)}>{copy.reschedule}</Button>}</div>}
          {booking.state === 'confirmed' && <p>{copy.reminder}: {status(booking.reminder_state)}{booking.reminder_error ? ' · ' + ((copy.reasons as Record<string, string>)[booking.reminder_error] ?? copy.error) : ''}</p>}
          {booking.state === 'confirmed' && booking.reminder_state === 'blocked' && <Button disabled={busy} onClick={() => void act(() => api.change(booking.id, 'retry-reminder'))}>{copy.retryReminder}</Button>}
        </Card></li>)}</ul>
      </section>
    </>}
  </div>;
}
