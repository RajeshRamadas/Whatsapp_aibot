'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import { usageProjection } from '@/lib/usage-projection';
import copy from '@/messages/billing.en.json';

type Policy = 'pause' | 'overage';
type Usage = {
  month: string; as_of: string; ai_messages: number; quota_behavior: Policy;
  limits: { ai_messages: number; overage_inr?: number };
};

const number = new Intl.NumberFormat('en-IN');
const date = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';

export function UsageMeter({ workspaceId, manage = false, revision = 0 }: {
  workspaceId: string; manage?: boolean; revision?: number;
}) {
  const [usage, setUsage] = useState<Usage>();
  const [retry, setRetry] = useState(0);
  const [loadError, setLoadError] = useState(false);
  const [choice, setChoice] = useState<Policy>();
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api<Usage>('usage', { headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal })
      .then(result => {
        if (!controller.signal.aborted) {
          setUsage(result);
          setLoadError(false);
        }
      })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, revision, retry]);

  async function save() {
    if (busy || !choice) return;
    setBusy(true);
    setSaved(false);
    setSaveError('');
    try {
      const result = await api<{ behavior: Policy }>('usage/quota-policy', {
        method: 'PUT', headers: { 'X-Workspace-Id': workspaceId }, body: JSON.stringify({ behavior: choice }),
      });
      setUsage(previous => previous && { ...previous, quota_behavior: result.behavior });
      setChoice(undefined);
      setSaved(true);
    } catch (failure) {
      setSaveError(failure instanceof Error ? failure.message : copy.policyError);
    } finally { setBusy(false); }
  }

  const used = usage?.ai_messages ?? 0;
  const limit = usage?.limits.ai_messages ?? 0;
  const percent = limit > 0 ? used / limit * 100 : 100;
  const forecast = usage ? usageProjection(usage.month, usage.as_of, used, limit) : null;
  return <Card className="space-y-3">
    <section aria-label={copy.usageTitle} className="space-y-3">
      <h2 className="font-semibold">{copy.usageTitle}</h2>
      {loadError && <div role="alert">
        <p>{copy.usageError}</p>
        <button className={field} onClick={() => { setLoadError(false); setRetry(value => value + 1); }}>
          {copy.retry}
        </button>
      </div>}
      {!usage && !loadError && <div role="status" aria-label={copy.loadingUsage}
        className="h-10 rounded bg-border motion-safe:animate-pulse" />}
      {usage && <>
        <p className="font-medium">{copy.used.replace('{used}', number.format(used)).replace('{limit}', number.format(limit))}</p>
        <meter className="block h-5 w-full" aria-label={copy.usageTitle}
          min={0} max={Math.max(limit, 1)} value={Math.min(used, limit)} />
        {percent < 100 && <p className="text-sm text-muted">
          {forecast ? copy.projection.replace('{date}', date.format(forecast))
            : used === 0 ? copy.noUsage : copy.belowProjection}
        </p>}
        {percent >= 80 && <div role="status" className={percent >= 100
          ? 'rounded-lg bg-red-100 p-3 text-red-950 dark:bg-red-950 dark:text-red-100'
          : 'rounded-lg bg-amber-100 p-3 text-amber-950 dark:bg-amber-950 dark:text-amber-100'}>
          <p className="font-medium">{percent >= 100 ? copy.limitReached : copy.limitWarning}</p>
          <p className="text-sm">{usage.quota_behavior === 'overage' ? copy.overageSummary : copy.pauseSummary}</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
            <Link href={manage ? '#plans' : '/dashboard/billing#plans'} className="underline">{copy.compare}</Link>
            <Link href={manage ? '#quota-policy' : '/dashboard/billing#quota-policy'}
              className="underline">{copy.choosePolicy}</Link>
          </div>
        </div>}
        {manage && <form id="quota-policy" className="scroll-mt-4 space-y-2 border-t border-border pt-3"
          onSubmit={event => { event.preventDefault(); void save(); }}>
          <fieldset className="space-y-2" disabled={busy}>
            <legend className="font-semibold">{copy.policyTitle}</legend>
            <label className="flex min-h-11 items-center gap-2">
              <input type="radio" name="quota-behavior" checked={(choice ?? usage.quota_behavior) === 'pause'}
                onChange={() => { setChoice('pause'); setSaved(false); }} />
              {copy.pause}
            </label>
            <label className="flex min-h-11 items-center gap-2">
              <input type="radio" name="quota-behavior" checked={(choice ?? usage.quota_behavior) === 'overage'}
                onChange={() => { setChoice('overage'); setSaved(false); }} />
              {copy.overage}
            </label>
            <p className="text-sm text-muted">{usage.limits.overage_inr != null
              ? copy.overageRate.replace('{amount}', number.format(usage.limits.overage_inr)) : copy.overageSummary}</p>
          </fieldset>
          {saveError && <p role="alert">{saveError}</p>}
          {saved && <p role="status">{copy.policySaved}</p>}
          <button className={field} disabled={busy || !choice || choice === usage.quota_behavior}>
            {copy.savePolicy}
          </button>
        </form>}
      </>}
    </section>
  </Card>;
}
