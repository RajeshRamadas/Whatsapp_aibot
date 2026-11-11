'use client';
import { useEffect, useState } from 'react';
import { webhookClient, type Delivery, type Endpoint } from '@/api/outgoing-webhooks';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';

export function WebhookHistory({ endpoint, revision, onResent }: { endpoint: Endpoint; revision: number; onResent: () => void }) {
  const copy = useCopy('webhooks'), locale = useDisplayLocale();
  const [items, setItems] = useState<Delivery[]>(), [cursor, setCursor] = useState<string | null>(), [error, setError] = useState(false);
  const [busy, setBusy] = useState(false), [reload, refresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError(false);
    webhookClient.deliveries(endpoint.id, undefined, controller.signal).then(result => { setItems(result.items); setCursor(result.next_cursor); }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [endpoint.id, revision, reload]);
  async function resend(delivery: Delivery) {
    if (!window.confirm(copy.resendPrompt)) return;
    setBusy(true); setError(false);
    try { await webhookClient.resend(endpoint.id, delivery.id, crypto.randomUUID()); onResent(); }
    catch { setError(true); } finally { setBusy(false); }
  }
  return <details className="border-t border-border pt-2"><summary className="min-h-11 cursor-pointer font-semibold">{copy.history}</summary><p className="text-sm text-muted">{copy.historyHelp}</p>
    <Button variant="secondary" disabled={busy} onClick={() => refresh(v => v + 1)}>{copy.refreshHistory}</Button>
    {error && <p role="alert">{copy.historyError}</p>}{!items && !error && <p role="status">{copy.loadingHistory}</p>}
    {items?.length === 0 && <p>{copy.noHistory}</p>}<ul className="space-y-3">{items?.map(item => <li key={item.id} className="mt-3 min-w-0 space-y-2 border-t border-border pt-3">
      <p className="break-all text-sm">{String(item.payload.type)} · {copy.states[item.state as keyof typeof copy.states] ?? item.state}</p>
      <p className="text-sm">{new Date(item.created_at).toLocaleString(locale, { timeZone: 'Asia/Calcutta' })} · {copy.attempts}: {item.attempts} · HTTP: {item.response_code ?? '—'}</p>
      {item.last_error && <p className="text-sm">{copy.failures[item.last_error as keyof typeof copy.failures] ?? copy.failure}</p>}
      <details><summary className="min-h-11 cursor-pointer">{copy.request}</summary><pre className="whitespace-pre-wrap break-all rounded bg-border p-3 text-xs">{JSON.stringify(item.payload, null, 2)}</pre>
        <ul className="space-y-1 text-sm">{item.history.map((attempt, index) => <li className="break-all" key={index}>{String(attempt.at)} · HTTP {String(attempt.code ?? '—')}</li>)}</ul></details>
      <Button variant="secondary" disabled={busy || !endpoint.enabled} onClick={() => void resend(item)}>{copy.resend}</Button>
    </li>)}</ul>
    {cursor && <Button variant="secondary" disabled={busy} onClick={async () => {
      setBusy(true); setError(false);
      try { const next = await webhookClient.deliveries(endpoint.id, cursor); setItems(values => [...(values ?? []), ...next.items]); setCursor(next.next_cursor); }
      catch { setError(true); } finally { setBusy(false); }
    }}>{copy.more}</Button>}
  </details>;
}
