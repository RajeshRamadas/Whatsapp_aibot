'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import copy from '@/messages/invoices.en.json';

type Details = { name: string; address: string; state: string; postal_code: string; gstin: string | null };
type Profile = { details: Details | null; invoicing_enabled: boolean; states: Record<string, string> };
type Invoice = { id: string; number: string; issued_on: string; gross_paise: number };
const empty: Details = { name: '', address: '', state: '', postal_code: '', gstin: null };
const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';
const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });

export function InvoiceSettings({ workspaceId }: { workspaceId: string }) {
  const [profile, setProfile] = useState<Profile>();
  const [details, setDetails] = useState<Details>(empty);
  const [invoices, setInvoices] = useState<Invoice[]>();
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    api<Profile>('billing/details', { headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal })
      .then(value => {
        if (!controller.signal.aborted) {
          setProfile(value);
          setDetails(value.details ?? empty);
        }
      })
      .catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => controller.abort();
  }, [workspaceId]);

  useEffect(() => {
    const controller = new AbortController();
    api<Invoice[]>('billing/invoices?limit=26&offset=' + offset, {
      headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal,
    }).then(value => { if (!controller.signal.aborted) setInvoices(value); })
      .catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => controller.abort();
  }, [workspaceId, offset]);

  function change(key: keyof Details, value: string) {
    setSaved(false);
    setDetails(previous => ({ ...previous, [key]: value }));
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    setSaved(false);
    setError('');
    try {
      await api('billing/details', { method: 'PUT', headers: { 'X-Workspace-Id': workspaceId },
        body: JSON.stringify({ ...details, gstin: details.gstin?.trim().toUpperCase() || null }) });
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.saveError);
    } finally {
      setBusy(false);
    }
  }

  async function download(invoice: Invoice) {
    setError('');
    try {
      const response = await fetch('/api/backend/billing/invoices/' + encodeURIComponent(invoice.id) + '/pdf',
        { headers: { 'X-Workspace-Id': workspaceId }, cache: 'no-store' });
      if (!response.ok) throw new Error(copy.downloadError);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'invoice-' + invoice.number.replace('/', '-') + '.pdf';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError(copy.downloadError);
    }
  }

  return <section className="space-y-4 rounded border border-border p-4" aria-label={copy.title}>
    <h2 className="font-semibold">{copy.title}</h2>
    {error && <p role="alert">{error}</p>}
    {!profile && !error && <p role="status">{copy.loading}</p>}
    {profile && <>
      <p className="text-sm">{profile.invoicing_enabled ? copy.beforeCheckout : copy.disabled}</p>
      <form className="grid max-w-lg gap-2" onSubmit={event => { event.preventDefault(); void save(); }}>
        <label className="grid gap-1">{copy.name}
          <input className={field} required maxLength={200} value={details.name}
            onChange={event => change('name', event.target.value)} />
        </label>
        <label className="grid gap-1">{copy.address}
          <textarea className={field} required maxLength={600} value={details.address}
            onChange={event => change('address', event.target.value)} />
        </label>
        <label className="grid gap-1">{copy.state}
          <select className={field} required value={details.state} onChange={event => change('state', event.target.value)}>
            <option value="">{copy.choose}</option>
            {Object.entries(profile.states).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </label>
        <label className="grid gap-1">{copy.postal}
          <input className={field} required inputMode="numeric" pattern="[1-9][0-9]{5}" value={details.postal_code}
            onChange={event => change('postal_code', event.target.value)} />
        </label>
        <label className="grid gap-1">{copy.gstin}
          <input className={field} maxLength={15} value={details.gstin ?? ''}
            onChange={event => change('gstin', event.target.value)} />
        </label>
        <button className={field} disabled={busy}>{copy.save}</button>
        {saved && <p role="status">{copy.saved}</p>}
      </form>
    </>}
    <h3 className="font-semibold">{copy.invoices}</h3>
    {invoices?.length === 0 && <p>{copy.empty}</p>}
    <ul className="space-y-2">
      {invoices?.slice(0, 25).map(invoice => <li key={invoice.id} className="flex flex-wrap items-center gap-2">
        <span>{invoice.number} · {invoice.issued_on} · {currency.format(invoice.gross_paise / 100)}</span>
        <button className={field} onClick={() => void download(invoice)}>{copy.download}</button>
      </li>)}
    </ul>
    <div className="flex gap-2">
      {offset > 0 && <button className={field} onClick={() => setOffset(value => value - 25)}>{copy.previous}</button>}
      {(invoices?.length ?? 0) > 25 &&
        <button className={field} onClick={() => setOffset(value => value + 25)}>{copy.next}</button>}
    </div>
  </section>;
}
