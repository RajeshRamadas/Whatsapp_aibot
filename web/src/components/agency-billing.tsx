'use client';

import { useEffect, useState } from 'react';
import { agency, type AgencyBilling } from '@/api/agency';
import { Card } from '@/components/ui/primitives';
import { useCopy, useDisplayLocale } from '@/i18n/provider';

const field = 'min-h-11 min-w-0 rounded border border-border bg-surface px-3 py-2';

export function AgencyBillingSettings({ workspace, changed }: { workspace: string; changed: () => void }) {
  const copy = useCopy('billing').agency;
  const number = new Intl.NumberFormat(useDisplayLocale());
  const [data, setData] = useState<AgencyBilling>();
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    agency(workspace).billing(controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); setLoadError(false); }
    }).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspace, revision]);

  async function update(client: string, link: boolean) {
    if (busy || !client) return;
    setError(''); setBusy(true);
    try {
      const request = agency(workspace);
      await (link ? request.link(client) : request.unlink(client));
      setSelected(''); setRevision(value => value + 1); changed();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.error);
    } finally { setBusy(false); }
  }

  return <Card className="space-y-3">
    <section aria-labelledby="agency-billing-heading" className="space-y-3">
      <h2 id="agency-billing-heading" className="font-semibold">{copy.title}</h2>
      {loadError && <div role="alert"><p>{copy.error}</p><button className={field} onClick={() => {
        setLoadError(false); setRevision(value => value + 1);
      }}>{copy.retry}</button></div>}
      {!data && !loadError && <p role="status">{copy.loading}</p>}
      {error && <p role="alert">{error}</p>}
      {data && <>
        <p>{copy.total.replace('{used}', number.format(data.ai_messages)).replace('{limit}', number.format(data.ai_message_limit))}</p>
        {data.clients.length ? <ul className="space-y-2" aria-label={copy.clients}>
          {data.clients.map(client => <li key={client.id} className="flex flex-wrap items-center gap-3">
            <span className="min-w-0 break-words">{client.name} · {copy.usage.replace('{used}', number.format(client.ai_messages))}</span>
            <button className={field} type="button" disabled={busy}
              aria-label={copy.removeName.replace('{name}', client.name)} onClick={() => {
                if (window.confirm(copy.confirmRemove.replace('{name}', client.name))) void update(client.id, false);
              }}>{copy.remove}</button>
          </li>)}
        </ul> : <p>{copy.noClients}</p>}
        {!!data.candidates.length && <form className="flex flex-wrap items-end gap-2" onSubmit={event => {
          event.preventDefault(); void update(selected, true);
        }}>
          <label className="grid min-w-0 gap-1">{copy.client}
            <select className={field} required value={selected} onChange={event => setSelected(event.target.value)} disabled={busy}>
              <option value="">{copy.choose}</option>
              {data.candidates.map(client => <option key={client.id} value={client.id}>{client.name}</option>)}
            </select>
          </label>
          <button className={field} disabled={busy || !selected}>{copy.add}</button>
        </form>}
      </>}
    </section>
  </Card>;
}
