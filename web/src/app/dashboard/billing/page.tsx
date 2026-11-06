'use client';

import { useEffect, useState } from 'react';
import { InvoiceSettings } from '@/components/invoice-settings';
import { UsageMeter } from '@/components/usage-meter';
import { useWorkspace } from '@/components/app-shell';
import { Badge, Card } from '@/components/ui/primitives';
import { api } from '@/lib/api';
import copy from '@/messages/billing.en.json';

type Subscription = {
  plan: string; status: string; checkout_url: string | null; period_end: string | null;
  grace_until: string | null; reconcile_issue: string | null; settlement_issue: string | null;
};
type Plan = {
  id: string; price_inr: number | null; available: boolean;
  limits: { ai_messages: number; channels?: number; seats?: number; files?: number | null };
};
type Billing = {
  enabled: boolean; trial_ends_at: string; current_plan: string; entitled: boolean;
  timezone: string; tax_rate: string | null; subscription: Subscription | null; plans: Plan[];
};
const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';
const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
const number = new Intl.NumberFormat('en-IN');
const name = (id: string) => id.charAt(0).toUpperCase() + id.slice(1);
const quotas = [
  ['ai_messages', copy.messages], ['channels', copy.channels], ['seats', copy.seats], ['files', copy.files],
] as const;

function WorkspaceBilling({ id }: { id: string }) {
  const [data, setData] = useState<Billing>();
  const [choice, setChoice] = useState<string>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const refresh = () => setRevision(value => value + 1);

  useEffect(() => {
    const controller = new AbortController();
    api<Billing>('billing', { headers: { 'X-Workspace-Id': id }, signal: controller.signal })
      .then(result => {
        if (!controller.signal.aborted) {
          setData(result);
          setLoadError(false);
        }
      })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [id, revision]);

  const subscription = data?.subscription;
  const active = subscription?.status === 'active';
  const inCheckout = !!subscription && ['created', 'creating', 'authenticated'].includes(subscription.status);
  const terminal = !!subscription && ['cancelled', 'completed', 'expired'].includes(subscription.status);
  const expired = terminal && (!subscription.period_end || Date.parse(subscription.period_end) <= Date.now());

  useEffect(() => {
    if (!inCheckout) return;
    const onReturn = () => setRevision(value => value + 1);
    window.addEventListener('focus', onReturn);
    return () => window.removeEventListener('focus', onReturn);
  }, [inCheckout]);

  const date = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeZone: data?.timezone ?? 'UTC' });
  const plans = [...(data?.plans ?? [])].sort((left, right) => (left.price_inr ?? 0) - (right.price_inr ?? 0));
  const current = plans.find(plan => plan.id === data?.current_plan);
  const chosen = plans.find(plan => plan.id === (inCheckout ? subscription.plan : choice))
    ?? plans.find(plan => plan.id === current?.id && plan.available)
    ?? plans.find(plan => plan.available);
  const downgrade = !!active && current?.price_inr != null && chosen?.price_inr != null
    && chosen.price_inr < current.price_inr;
  const reduced = downgrade && current && chosen ? quotas.flatMap(([key, label]) => {
    const before = current.limits[key];
    const after = chosen.limits[key];
    return typeof after === 'number' && (before === null || (typeof before === 'number' && after < before))
      ? [number.format(after) + ' ' + label.toLowerCase()] : [];
  }) : [];
  const reducedMessage = reduced.length ? copy.quotaReductions.replace('{limits}', reduced.join(', ')) : '';

  async function action(path: string, method: string, plan?: string, success = '') {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api(path, { method, headers: { 'X-Workspace-Id': id },
        body: plan ? JSON.stringify({ plan }) : undefined });
      setNotice(success);
      refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.changeError);
    } finally { setBusy(false); }
  }

  function changePlan() {
    if (!chosen) return;
    const confirmation = (downgrade ? copy.downgradeConfirm : copy.upgradeConfirm)
      .replace('{plan}', name(chosen.id))
      .replace('{messages}', number.format(chosen.limits.ai_messages));
    if (window.confirm([confirmation, reducedMessage].filter(Boolean).join('\n\n'))) {
      void action('billing/subscription', 'PATCH', chosen.id, downgrade ? copy.downgradeScheduled : '');
    }
  }

  return <main className="mx-auto max-w-6xl space-y-5 p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.billing}</h1>
    <UsageMeter workspaceId={id} manage revision={revision} />
    {loadError && <div role="alert">
      <p>{copy.loadError}</p>
      <button className={field} onClick={() => { setLoadError(false); refresh(); }}>{copy.retry}</button>
    </div>}
    {!data && !loadError && <p role="status">{copy.loading}</p>}
    {data && <>
      <Card className="space-y-2">
        <h2 className="font-semibold">{copy.subscription}</h2>
        <p>{copy.current}: {name(data.current_plan)}</p>
        {!subscription && <p>{copy.trial}: {date.format(new Date(data.trial_ends_at))}</p>}
        {!data.entitled && <p role="status">{copy.paused}</p>}
        {active && data.entitled && <p role="status">{copy.active}</p>}
        {subscription && <>
          <p>{copy.subscription}: {name(subscription.status)}</p>
          {subscription.period_end && <p>{copy.periodEnds}: {date.format(new Date(subscription.period_end))}</p>}
          {subscription.grace_until && <p>{copy.graceEnds}: {date.format(new Date(subscription.grace_until))}</p>}
          {inCheckout && subscription.checkout_url && <a className="inline-flex min-h-11 items-center underline"
            href={subscription.checkout_url} target="_blank" rel="noopener noreferrer">{copy.continueCheckout}</a>}
          {subscription.reconcile_issue && <p role="status">{subscription.reconcile_issue}</p>}
          {subscription.settlement_issue && <p role="status">{subscription.settlement_issue}</p>}
        </>}
        {data.enabled && <div className="flex flex-wrap gap-2">
          {subscription && !terminal && subscription.status !== 'creating' &&
            <button className={field} disabled={busy} onClick={() => {
              if (window.confirm(copy.cancelConfirm)) void action('billing/subscription/cancel', 'POST', undefined,
                copy.cancelRequested);
            }}>{copy.cancel}</button>}
          <button className={field} disabled={busy} onClick={refresh}>{copy.refresh}</button>
        </div>}
      </Card>
      <Card id="plans" className="scroll-mt-4 space-y-3">
        <fieldset className="space-y-3" disabled={busy || inCheckout || !data.enabled}>
          <legend className="font-semibold">{copy.plansTitle}</legend>
          <div className="grid gap-3 md:grid-cols-3">
            {plans.map(plan => <label key={plan.id} className="block rounded-lg border border-border p-4
              has-[:checked]:border-accent has-[:checked]:bg-accent/5">
              <span className="flex flex-wrap items-center gap-2">
                <input type="radio" name="plan" checked={chosen?.id === plan.id} disabled={!plan.available}
                  aria-label={name(plan.id)} onChange={() => { setChoice(plan.id); setNotice(''); }} />
                <strong>{name(plan.id)}</strong>
                {plan.id === data.current_plan && <Badge tone="success">{copy.current}</Badge>}
              </span>
              <p className="my-2 font-medium">{plan.price_inr != null
                ? <>{currency.format(plan.price_inr)} {copy.monthly}{data.tax_rate ? ' · ' + copy.tax : ''}</>
                : copy.unavailable}</p>
              <ul className="space-y-1 text-sm">
                {quotas.map(([key, label]) => plan.limits[key] !== undefined && <li key={key}>
                  {plan.limits[key] === null ? copy.unlimited : number.format(plan.limits[key]!)} {label.toLowerCase()}
                </li>)}
              </ul>
              {!plan.available && <p className="mt-2 text-sm text-muted">{copy.unavailable}</p>}
            </label>)}
          </div>
        </fieldset>
        {!data.enabled && <p>{copy.providerUnavailable}</p>}
        {reducedMessage && <p className="text-sm">{reducedMessage}</p>}
        {data.enabled && chosen?.available && active &&
          <button className={field} disabled={busy || chosen.id === current?.id} onClick={changePlan}>
            {downgrade ? copy.downgrade : copy.upgrade}
          </button>}
        {data.enabled && chosen?.available && (!subscription || expired || (inCheckout && !subscription.checkout_url)) &&
          <button className={field} disabled={busy}
            onClick={() => void action('billing/checkout', 'POST', chosen.id)}>
            {inCheckout ? copy.check : copy.start}
          </button>}
      </Card>
    </>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <InvoiceSettings workspaceId={id} revision={revision} />
  </main>;
}

export default function BillingPage() {
  const workspace = useWorkspace();
  return workspace?.role === 'owner'
    ? <WorkspaceBilling key={workspace.id} id={workspace.id} />
    : <p className="p-4">{copy.owner}</p>;
}
