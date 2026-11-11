'use client';
import { useEffect, useState } from 'react';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import type { Merchant, PaymentClient } from '@/api/payments';
import type { Action, Template } from '@/api/appointments';
import { Button } from './ui/button';
import { field } from './appointment-settings';
export function PaymentConnection({ merchants, templates, api, act, busy, timezone }: {
  merchants: Merchant[]; templates: Template[]; api: PaymentClient; act: Action; busy: boolean; timezone: string;
}) {
  const copy = useCopy('payments'), locale = useDisplayLocale();
  const [label, setLabel] = useState(''), [key, setKey] = useState(''), [secret, setSecret] = useState('');
  const [webhook, setWebhook] = useState(''), [live, setLive] = useState(false), [template, setTemplate] = useState('');
  const [origin, setOrigin] = useState('');
  useEffect(() => { setOrigin(location.origin); }, []);
  const status = (state: string) => (copy.states as Record<string, string>)[state] ?? copy.error;
  const active = merchants.some(c => c.status !== 'disconnected');
  return <details className="rounded-xl border border-border p-4">
    <summary className="min-h-11 cursor-pointer py-2"><h2 className="inline font-semibold">{copy.connection}</h2></summary>
    <div className="mt-4 space-y-4"><p>{copy.connectionHelp}</p>
      <form aria-label={copy.connect} className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={async e => {
        e.preventDefault();
        if (await act(() => api.connect({ label, key_id: key, key_secret: secret, webhook_secret: webhook,
          confirm_live: live, receipt_template_id: template || null }))) { setKey(''); setSecret(''); setWebhook(''); setLive(false); }
      }}>
        <label className="grid min-w-0 gap-1">{copy.label}<input className={field} required maxLength={100} value={label} disabled={busy || active} onChange={e => setLabel(e.target.value)} /></label>
        <label className="grid min-w-0 gap-1">{copy.key}<input className={field} type="password" autoComplete="off" required value={key} disabled={busy || active} onChange={e => setKey(e.target.value)} /></label>
        <label className="grid min-w-0 gap-1">{copy.secret}<input className={field} type="password" autoComplete="new-password" minLength={8} maxLength={200} required value={secret} disabled={busy || active} onChange={e => setSecret(e.target.value)} /></label>
        <label className="grid min-w-0 gap-1">{copy.webhookSecret}<input className={field} type="password" autoComplete="new-password" minLength={16} maxLength={200} required value={webhook} disabled={busy || active} onChange={e => setWebhook(e.target.value)} /></label>
        <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.receiptTemplate}<select className={field} value={template} disabled={busy || active} onChange={e => setTemplate(e.target.value)}>
          <option value="">{copy.noTemplate}</option>{templates.filter(t => t.category === 'UTILITY' && t.body && new Set(t.body.match(/\{\{\d+\}\}/g)).size === 3).map(t => <option key={t.id} value={t.id}>{t.name} · {t.language}</option>)}</select></label>
        <p className="text-sm text-muted sm:col-span-2">{copy.templateHelp}</p>
        <label className="flex min-h-11 items-start gap-2 sm:col-span-2"><input className="mt-1" type="checkbox" checked={live} disabled={busy || active} onChange={e => setLive(e.target.checked)} />{copy.liveConsent}</label>
        <Button disabled={busy || active}>{copy.connect}</Button>
      </form>
      {merchants.length === 0 && <p>{copy.emptyConnections}</p>}
      <ul className="space-y-3">{merchants.map(c => <li key={c.id} className="min-w-0 space-y-2 break-words rounded-lg border border-border p-3">
        <h3 className="font-semibold">{c.label} · {status(c.status)} · {status(c.mode)}</h3><p>{copy.keyEnding}: {c.key_last4}</p>
        <p>{copy.lastSuccess}: {c.last_success_at ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(c.last_success_at)) : copy.never}</p>
        {c.last_error && <p role="status">{copy.attention}</p>}
        <label className="grid min-w-0 gap-1">{copy.webhookURL}<input className={field} readOnly value={origin + '/api/backend/payments/webhook/' + c.id} /></label>
        <p className="text-sm text-muted">{copy.events}</p>
        {c.status !== 'disconnected' && <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => void act(() => api.test(c.id))}>{copy.testConnection}</Button>
          <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.disconnectPrompt)) void act(() => api.disconnect(c.id)); }}>{copy.disconnect}</Button></div>}
      </li>)}</ul>
    </div>
  </details>;
}
