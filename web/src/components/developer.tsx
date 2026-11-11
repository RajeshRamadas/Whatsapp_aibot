'use client';
import { useEffect, useState } from 'react';
import { developerClient, scopes, type ApiKey, type Scope } from '@/api/developer';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';

export function Developer({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('developer'), locale = useDisplayLocale();
  const [keys, setKeys] = useState<ApiKey[]>(), [loadError, setLoadError] = useState(false), [version, refresh] = useState(0);
  const [name, setName] = useState(''), [selected, setSelected] = useState<Scope[]>(['knowledge:read']), [days, setDays] = useState(90);
  const [secret, setSecret] = useState(''), [busy, setBusy] = useState(false), [failed, setFailed] = useState(false), [copied, setCopied] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setLoadError(false);
    developerClient.list(controller.signal).then(setKeys).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  function date(value: string | null) { return value ? new Date(value).toLocaleDateString(locale, { timeZone: 'Asia/Calcutta' }) : copy.never; }
  return <div className="mt-6 min-w-0 space-y-6">
    {failed && <p role="alert">{copy.actionError}</p>}
    {secret && <section className="space-y-3 rounded-xl border border-border bg-surface p-4" aria-label={copy.secretTitle}>
      <h2 className="text-xl font-semibold">{copy.secretTitle}</h2><p>{copy.secretHelp}</p>
      <div className="grid gap-1"><label htmlFor="new-api-key">{copy.secretLabel}</label><textarea id="new-api-key" className={field + ' break-all font-mono'} readOnly rows={3} value={secret} autoComplete="off" spellCheck={false} /></div>
      <div className="flex flex-wrap gap-3"><Button onClick={async () => { try { await navigator.clipboard.writeText(secret); setCopied(true); } catch { setFailed(true); } }}>{copy.copy}</Button>
        <Button variant="secondary" onClick={() => { setSecret(''); setCopied(false); }}>{copy.dismiss}</Button></div>
      {copied && <p role="status">{copy.copied}</p>}
    </section>}
    <form aria-label={copy.create} className="space-y-4 rounded-xl border border-border p-4" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setFailed(false); setSecret(''); setCopied(false);
      try { const result = await developerClient.create(name.trim(), selected, days); setSecret(result.key); setName(''); refresh(v => v + 1); }
      catch { setFailed(true); } finally { setBusy(false); }
    }}><h2 className="text-xl font-semibold">{copy.create}</h2><p className="text-sm text-muted">{copy.scopeHelp}</p>
      <div className="grid gap-4 sm:grid-cols-2"><label className="grid gap-1">{copy.name}<input className={field} required maxLength={100} disabled={busy} value={name} onChange={e => setName(e.target.value)} autoComplete="off" /></label>
        <label className="grid gap-1">{copy.expiry}<select className={field} value={days} disabled={busy} onChange={e => setDays(Number(e.target.value))}>{[30, 90, 365].map(value => <option key={value} value={value}>{copy.days.replace('{days}', String(value))}</option>)}</select></label></div>
      <fieldset disabled={busy}><legend className="mb-2 font-medium">{copy.scopesTitle}</legend><div className="grid gap-1 sm:grid-cols-2">{scopes.map(scope => <label key={scope} className="flex min-h-11 items-center gap-3 rounded-lg border border-border px-3 py-2">
        <input type="checkbox" checked={selected.includes(scope)} onChange={e => setSelected(values => e.target.checked ? [...values, scope] : values.filter(value => value !== scope))} /><span>{copy.scopes[scope]}</span></label>)}</div></fieldset>
      <Button disabled={busy || !name.trim() || !selected.length}>{busy ? copy.saving : copy.create}</Button>
    </form>
    <section className="space-y-3" aria-label={copy.keysTitle}><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">{copy.keysTitle}</h2><Button variant="secondary" disabled={busy} onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button></div>
      {!keys && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError}</p>}
      {keys?.length === 0 && <p>{copy.empty}</p>}
      {keys?.map(key => <article key={key.id} className="min-w-0 space-y-2 rounded-xl border border-border p-4"><h3 className="break-words font-semibold">{key.name}</h3>
        <p className="break-all font-mono text-sm">{key.key_prefix}…</p><p className="text-sm">{key.revoked_at ? copy.revoked : key.expires_at && new Date(key.expires_at) <= new Date() ? copy.expired : copy.active}</p>
        <p className="text-sm">{copy.expires}: {date(key.expires_at)} · {copy.lastUsed}: {date(key.last_used_at)}</p>
        <ul className="flex flex-wrap gap-2 text-sm">{key.scopes.map(scope => <li className="break-all rounded bg-border px-2 py-1" key={scope}>{copy.scopes[scope as Scope] ?? scope}</li>)}</ul>
        {!key.revoked_at && <Button variant="destructive" disabled={busy} aria-label={copy.revokeName.replace('{name}', key.name)} onClick={async () => {
          if (!window.confirm(copy.revokePrompt)) return;
          setBusy(true); setFailed(false); setSecret(''); setCopied(false);
          try { await developerClient.revoke(key.id); refresh(v => v + 1); } catch { setFailed(true); } finally { setBusy(false); }
        }}>{copy.revoke}</Button>}
      </article>)}
    </section>
  </div>;
}

export function ApiReference() {
  const copy = useCopy('developer');
  return <section className="mt-8 min-w-0 space-y-4 rounded-xl border border-border p-4" aria-label={copy.reference}>
    <h2 className="text-xl font-semibold">{copy.reference}</h2><p>{copy.baseHelp}</p>
    <code className="block break-all rounded bg-border p-3 text-sm">/api/backend/public</code>
    <ul className="list-disc space-y-2 pl-5"><li>{copy.authHelp}</li><li>{copy.paginationHelp}</li><li>{copy.replayHelp}</li><li>{copy.rateHelp}</li><li>{copy.jobsHelp}</li><li>{copy.sendHelp}</li><li>{copy.runHelp}</li><li>{copy.versionHelp}</li></ul>
    <div className="flex flex-wrap gap-4"><a className="inline-flex min-h-11 items-center underline" href="/api/backend/public/openapi.json" download="marketingadda-public-v1.json">{copy.downloadSchema}</a>
      <a className="inline-flex min-h-11 items-center underline" href="/api/backend/public/docs" target="_blank" rel="noreferrer">{copy.interactiveDocs}</a></div>
    <p className="text-sm text-muted">{copy.exampleHelp}</p>
    <pre className="whitespace-pre-wrap break-all rounded bg-border p-3 text-xs">{'curl "$API_BASE/contacts?limit=30" \\\n  -H "Authorization: Bearer $API_KEY"\n\ncurl "$API_BASE/contacts" \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -H "Idempotency-Key: contact-001" \\\n  -d \'{"channel":"web","external_user":"customer-001"}\''}</pre>
  </section>;
}
