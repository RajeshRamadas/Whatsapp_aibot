'use client';
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { Language } from '@/api/appointments';
import { paymentClient, paise, type PaymentData } from '@/api/payments';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { Badge } from './ui/primitives';
import { field } from './appointment-settings';
import { PaymentConnection } from './payment-connection';
export function Payments({ workspaceId, admin }: { workspaceId: string; admin: boolean }) {
  const copy = useCopy('payments'), appointmentCopy = useCopy('appointments'), locale = useDisplayLocale();
  const api = useMemo(() => paymentClient(workspaceId), [workspaceId]);
  const [data, setData] = useState<PaymentData>(), [error, setError] = useState(false), [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false), [revision, refresh] = useState(0), [saved, setSaved] = useState(false);
  const [connection, setConnection] = useState(''), [conversation, setConversation] = useState(''), [booking, setBooking] = useState('');
  const [name, setName] = useState(''), [phone, setPhone] = useState(''), [value, setValue] = useState('');
  const [description, setDescription] = useState(''), [expiry, setExpiry] = useState(''), [language, setLanguage] = useState('en');
  const [approval, setApproval] = useState('');
  useEffect(() => { setApproval(crypto.randomUUID()); const d = new Date(Date.now() + 86400000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); setExpiry(d.toISOString().slice(0, 16)); }, []);
  useEffect(() => {
    const controller = new AbortController();
    api.load(admin, controller.signal).then(v => { if (!controller.signal.aborted) { setData(v); setLoadError(false); } })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    const timer = setTimeout(() => refresh(v => v + 1), 10000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, admin, revision]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError(false); setSaved(false);
    try { await operation(); setSaved(true); refresh(v => v + 1); return true; }
    catch { setError(true); return false; }
    finally { setBusy(false); }
  }
  const status = (state: string) => (copy.states as Record<string, string>)[state] ?? copy.error;
  const money = (amount: number) => new Intl.NumberFormat(locale, { style: 'currency', currency: 'INR' }).format(amount / 100);
  return <div className="mt-5 min-w-0 space-y-5">
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}
    {loadError && <p role="alert">{copy.loadError} <Button onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></p>}
    {error && <p role="alert">{copy.error}</p>}{saved && <p role="status">{copy.saved}</p>}
    {data && <>
      {admin && <PaymentConnection merchants={data.merchants} templates={data.templates} api={api} act={act} busy={busy} timezone={data.catalog.timezone} />}
      <form aria-label={copy.approve} className="grid min-w-0 grid-cols-1 gap-3 rounded-xl border border-border p-4 sm:grid-cols-2" onSubmit={async e => {
        e.preventDefault(); const amount = paise(value); if (amount === null) { setError(true); return; }
        if (await act(() => api.approve({ connection_id: connection, conversation_id: conversation, booking_id: booking || null,
          approval_key: approval, amount, description, customer_name: name, customer_phone: phone,
          language: language as Language, expires_at: new Date(expiry).toISOString() }))) {
          setApproval(crypto.randomUUID()); setValue(''); setDescription(''); setName(''); setPhone(''); setBooking('');
        }
      }}>
        <h2 className="font-semibold sm:col-span-2">{copy.approve}</h2><p className="text-sm text-muted sm:col-span-2">{copy.approvalHelp}</p>
        <label className="grid min-w-0 gap-1">{copy.merchant}<select className={field} required value={connection} disabled={busy} onChange={e => setConnection(e.target.value)}>
          <option value="">{copy.choose}</option>{data.catalog.connections.filter(c => c.status === 'connected').map(c => <option key={c.id} value={c.id}>{c.label} · {status(c.mode)}</option>)}</select></label>
        <label className="grid min-w-0 gap-1">{copy.conversation}<select className={field} required value={conversation} disabled={busy} onChange={e => {
          setConversation(e.target.value); setBooking(''); const c = data.conversations.find(c => c.id === e.target.value); setPhone(c?.channel === 'whatsapp' ? c.user || '' : '');
        }}><option value="">{copy.choose}</option>{data.conversations.map(c => <option key={c.id} value={c.id}>{c.user || c.channel} · {c.id.slice(0, 8)}</option>)}</select></label>
        <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.booking}<select className={field} value={booking} disabled={busy || !conversation} onChange={e => setBooking(e.target.value)}>
          <option value="">{copy.noBooking}</option>{data.bookings.filter(b => b.conversation_id === conversation && ['held', 'confirming', 'confirmed'].includes(b.state)).map(b => <option key={b.id} value={b.id}>{b.service} · {b.id.slice(0, 8)}</option>)}</select></label>
        <label className="grid gap-1">{copy.amount}<input className={field} inputMode="decimal" required pattern="[0-9]+(\.[0-9]{1,2})?" value={value} disabled={busy} onChange={e => setValue(e.target.value)} /></label>
        <label className="grid gap-1">{copy.description}<input className={field} required maxLength={255} value={description} disabled={busy} onChange={e => setDescription(e.target.value)} /></label>
        <label className="grid gap-1">{copy.customerName}<input className={field} required maxLength={100} value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
        <label className="grid gap-1">{copy.customerPhone}<input className={field} type="tel" required pattern="\+?[1-9][0-9]{6,14}" value={phone} disabled={busy} onChange={e => setPhone(e.target.value)} /></label>
        <label className="grid gap-1">{copy.expiry}<input className={field} type="datetime-local" required value={expiry} disabled={busy} onChange={e => setExpiry(e.target.value)} /></label>
        <label className="grid min-w-0 gap-1">{copy.language}<select className={field} value={language} disabled={busy} onChange={e => setLanguage(e.target.value)}>
          {Object.entries(appointmentCopy.languages).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>
        <p className="text-sm text-muted sm:col-span-2">{copy.timeHelp.replace('{timezone}', data.catalog.timezone)}</p>
        <Button disabled={busy || !approval || !data.catalog.connections.some(c => c.status === 'connected')}>{copy.approve}</Button>
      </form>
      <section aria-label={copy.requests} className="space-y-3"><h2 className="text-xl font-semibold">{copy.requests}</h2><p className="text-sm text-muted">{copy.limit}</p>
        {data.requests.length === 0 && <p>{copy.emptyRequests}</p>}
        <ul className="grid gap-3 lg:grid-cols-2">{data.requests.map(v => <li key={v.id} className="min-w-0 space-y-3 break-words rounded-xl border border-border p-4">
          <h3 className="font-semibold">{v.description} · {money(v.amount)}</h3><Badge>{status(v.state)}</Badge>
          <p>{copy.expiry}: {new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: data.catalog.timezone }).format(new Date(v.expires_at))}</p>
          {v.last_error && <p role="status">{copy.operationError}</p>}
          {v.url && v.state === 'created' && <a className="inline-block min-h-11 underline" href={v.url} target="_blank" rel="noreferrer">{copy.openLink}</a>}
          {v.state === 'paid' && <p>{copy.reference}: {v.payment_id || v.provider_id}</p>}
          <p>{copy.linkDelivery}: {v.link_delivery ? status(v.link_delivery) : copy.notSent}</p><p>{copy.receiptDelivery}: {v.receipt_delivery ? status(v.receipt_delivery) : copy.notSent}</p>
          {v.conversation_id && <Link className="inline-block min-h-11 underline" href={'/dashboard/inbox?conversation=' + v.conversation_id}>{copy.openConversation}</Link>}
          <div className="flex flex-wrap gap-2">{['approved', 'failed'].includes(v.state) && <Button disabled={busy || ['queued', 'working'].includes(v.job_state)} onClick={() => {
            if (window.confirm(copy.sendPrompt.replace('{amount}', money(v.amount)).replace('{description}', v.description))) void act(() => api.change(v.id, 'send'));
          }}>{copy.send}</Button>}
            {!['paid', 'cancelled', 'expired'].includes(v.state) && <><Button variant="secondary" disabled={busy || ['queued', 'working'].includes(v.job_state)} onClick={() => void act(() => api.change(v.id, 'sync'))}>{copy.sync}</Button>
              <Button variant="secondary" disabled={busy || v.state === 'cancelling'} onClick={() => { if (window.confirm(copy.cancelPrompt)) void act(() => api.change(v.id, 'cancel')); }}>{copy.cancel}</Button></>}
          </div>
        </li>)}</ul>
      </section>
    </>}
  </div>;
}
