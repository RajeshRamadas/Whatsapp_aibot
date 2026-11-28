'use client';

import { useEffect, useState } from 'react';
import { agency, type BrandSettings, type BrandView } from '@/api/agency';
import { Card } from '@/components/ui/primitives';
import { useCopy } from '@/i18n/provider';

const field = 'min-h-11 min-w-0 rounded border border-border bg-surface px-3 py-2';

export function AgencyBrandingSettings({ workspace }: { workspace: string }) {
  const copy = useCopy('billing').brand;
  const [saved, setSaved] = useState<BrandView>();
  const [draft, setDraft] = useState<BrandSettings>();
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<'saved' | 'verified'>();
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);

  function apply(view: BrandView) {
    setSaved(view);
    setDraft({ display_name: view.display_name, logo_url: view.logo_url ?? '',
      domain: view.domain ?? '', primary_color: view.primary_color, secondary_color: view.secondary_color });
  }
  useEffect(() => {
    const controller = new AbortController();
    agency(workspace).branding(controller.signal).then(view => {
      if (!controller.signal.aborted) apply(view);
    }).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspace, retry]);

  async function submit(verify: boolean) {
    if (!draft || busy) return;
    setBusy(true); setError(''); setStatus(undefined);
    try {
      const client = agency(workspace);
      const view = await (verify ? client.verifyDomain() : client.saveBrand({ ...draft,
        domain: draft.domain || null, logo_url: draft.logo_url || null }));
      apply(view); setStatus(verify ? 'verified' : 'saved');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.saveError);
    } finally { setBusy(false); }
  }

  return <Card className="space-y-3">
    <section className="space-y-3" aria-labelledby="agency-brand-heading">
      <h2 id="agency-brand-heading" className="font-semibold">{copy.title}</h2>
      {error && <p role="alert">{error}</p>}
      {!draft && (loadError ? <div role="alert"><p>{copy.loadError}</p>
        <button type="button" className={field} onClick={() => {
          setLoadError(false); setRetry(value => value + 1);
        }}>{copy.retry}</button></div> : <p role="status">{copy.loading}</p>)}
      {draft && <form className="grid gap-3 sm:grid-cols-2" onSubmit={event => {
        event.preventDefault(); void submit(false);
      }}>
        <label className="grid min-w-0 gap-1">{copy.name}
          <input className={field} required maxLength={120} disabled={busy} value={draft.display_name}
            onChange={event => setDraft({ ...draft, display_name: event.target.value })} />
        </label>
        <label className="grid min-w-0 gap-1">{copy.logo}
          <input className={field} type="url" maxLength={500} disabled={busy} value={draft.logo_url ?? ''}
            onChange={event => setDraft({ ...draft, logo_url: event.target.value })} />
        </label>
        <label className="grid min-w-0 gap-1">{copy.primary}
          <input className={field} type="color" disabled={busy} value={draft.primary_color}
            onChange={event => setDraft({ ...draft, primary_color: event.target.value })} />
        </label>
        <label className="grid min-w-0 gap-1">{copy.secondary}
          <input className={field} type="color" disabled={busy} value={draft.secondary_color}
            onChange={event => setDraft({ ...draft, secondary_color: event.target.value })} />
        </label>
        <label className="grid min-w-0 gap-1 sm:col-span-2">{copy.domain}
          <input className={field} maxLength={253} disabled={busy} value={draft.domain ?? ''}
            onChange={event => setDraft({ ...draft, domain: event.target.value })} />
        </label>
        <div className="sm:col-span-2"><button className={field} disabled={busy}>{copy.save}</button></div>
      </form>}
      {saved?.domain && (saved.verified_at ? <p>{copy.live.replace('{domain}', saved.domain)}</p> :
        <div className="space-y-2">
          <p>{copy.dns}</p>
          <dl className="space-y-1 break-all">
            <div><dt className="font-medium">{copy.recordName}</dt><dd><code>{saved.dns_name}</code></dd></div>
            <div><dt className="font-medium">{copy.recordValue}</dt><dd><code>{saved.dns_value}</code></dd></div>
          </dl>
          <button type="button" className={field} disabled={busy || draft?.domain !== saved.domain}
            onClick={() => void submit(true)}>{copy.verify}</button>
        </div>)}
      {status && <p role="status">{copy[status]}</p>}
    </section>
  </Card>;
}
