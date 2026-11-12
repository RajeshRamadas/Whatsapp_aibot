'use client';
import { useEffect, useState } from 'react';
import { commerceClient, type StoreInput, type StoreView } from '@/api/commerce';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';

export function CommerceStores({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('commerce'), locale = useDisplayLocale();
  const [rows, setRows] = useState<StoreView[]>(), [version, refresh] = useState(0);
  const [loadError, setLoadError] = useState(false), [error, setError] = useState(false), [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState('shopify'), [url, setUrl] = useState('');
  const [token, setToken] = useState(''), [consumerKey, setConsumerKey] = useState(''), [readOnly, setReadOnly] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setLoadError(false);
    commerceClient.list(controller.signal).then(setRows).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  const connected = rows?.some(row => row.status === 'connected');
  useEffect(() => { if (!connected) return; const timer = setInterval(() => refresh(v => v + 1), 5000); return () => clearInterval(timer); }, [connected]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(false);
    try { await fn(); refresh(v => v + 1); } catch { setError(true); } finally { setBusy(false); }
  }
  const stamp = (value: string | null) => value ? new Date(value).toLocaleString(locale) : copy.never;
  return <section className="mt-6 min-w-0 space-y-5" aria-label={copy.title}>
    <h2 className="text-xl font-semibold">{copy.title}</h2><p>{copy.intro}</p><p className="text-sm text-muted">{copy.privacy}</p>
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!rows && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError}</p>}
    {error && <p role="alert">{copy.actionError}</p>}
    {rows?.map(row => <section key={row.id} className="min-w-0 space-y-3 rounded-xl border border-border p-4" aria-label={row.url}>
      <h3 className="break-all text-lg font-semibold">{row.url}</h3>
      <p role="status">{row.status === 'connected' ? copy.connected : copy.needsTest}{row.lease && ' · ' + copy.syncing}</p>
      <p>{copy.products}: {row.product_count}</p><p>{copy.lastSync}: {stamp(row.last_success)}</p><p>{copy.nextSync}: {stamp(row.next_run)}</p>
      {row.last_error && <p role="alert">{copy.syncError}</p>}
      <div className="flex flex-wrap gap-3">
        <Button disabled={busy} onClick={() => void act(() => commerceClient.test(row.id))}>{copy.test}</Button>
        <Button variant="secondary" disabled={busy || row.status !== 'connected' || !!row.lease} onClick={() => void act(() => commerceClient.sync(row.id))}>{copy.sync}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.disconnectPrompt)) void act(() => commerceClient.disconnect(row.id)); }}>{copy.disconnect}</Button>
      </div>
    </section>)}
    {rows?.length === 0 && <><p>{copy.empty}</p><form className="grid max-w-lg gap-4" onSubmit={e => {
      e.preventDefault(); void act(async () => { await commerceClient.connect({ provider, url, token, consumer_key: provider === 'woocommerce' ? consumerKey : '', read_only: readOnly } as StoreInput); setToken(''); setConsumerKey(''); setReadOnly(false); });
    }}>
      <label className="grid gap-1">{copy.provider}<select className={field} value={provider} onChange={e => { setProvider(e.target.value); setToken(''); setConsumerKey(''); setReadOnly(false); }}><option value="shopify">Shopify</option><option value="woocommerce">WooCommerce</option></select></label>
      <label className="grid gap-1">{copy.url}<input type="url" required maxLength={255} className={field} value={url} onChange={e => setUrl(e.target.value)} autoComplete="off" /></label>
      {provider === 'woocommerce' && <label className="grid gap-1">{copy.key}<input required maxLength={100} className={field} value={consumerKey} onChange={e => setConsumerKey(e.target.value)} autoComplete="off" /></label>}
      <label className="grid gap-1">{provider === 'shopify' ? copy.token : copy.secret}<input type="password" required minLength={8} maxLength={4096} className={field} value={token} onChange={e => setToken(e.target.value)} autoComplete="new-password" /></label>
      <p className="text-sm text-muted">{provider === 'shopify' ? copy.shopifyHelp : copy.wooHelp}</p>
      <label className="flex min-h-11 items-center gap-3"><input type="checkbox" required checked={readOnly} onChange={e => setReadOnly(e.target.checked)} />{copy.readOnly}</label>
      <Button disabled={busy || !readOnly}>{busy ? copy.saving : copy.connect}</Button>
    </form></>}
  </section>;
}
