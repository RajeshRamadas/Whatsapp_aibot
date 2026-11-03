'use client';

import { useEffect, useState } from 'react';
import { useWorkspace } from '@/components/app-shell';
import { api } from '@/lib/api';

type Subscription = {
  plan: string; status: string; checkout_url: string | null; period_end: string | null;
  grace_until: string | null; reconcile_issue: string | null; settlement_issue: string | null;
};
type Billing = {
  enabled: boolean; trial_ends_at: string; current_plan: string; entitled: boolean;
  subscription: Subscription | null; plans: { id: string; price_inr: number; available: boolean }[];
};

function WorkspaceBilling({ id }: { id: string }) {
  const [data, setData] = useState<Billing>();
  const [choice, setChoice] = useState('starter');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api<Billing>('billing', { headers: { 'X-Workspace-Id': id }, signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(() => { if (!controller.signal.aborted) setError('Could not load billing.'); });
    return () => controller.abort();
  }, [id, revision]);

  async function action(path: string, method: string, plan?: string) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await api(path, { method, headers: { 'X-Workspace-Id': id },
        body: plan ? JSON.stringify({ plan }) : undefined });
      setRevision(value => value + 1);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not update the subscription.');
    } finally { setBusy(false); }
  }

  const subscription = data?.subscription;
  const active = subscription?.status === 'active';
  const inCheckout = !!subscription && ['created', 'creating', 'authenticated'].includes(subscription.status);
  const terminal = !!subscription && ['cancelled', 'completed', 'expired'].includes(subscription.status);
  const plan = data?.plans.find(row => row.id === choice);
  const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';
  return <main className="space-y-4 p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">Billing</h1>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">Loading billing…</p>}
    {data && <>
      <p>Current plan: {data.current_plan}</p>
      <p>Trial ends: {new Date(data.trial_ends_at).toLocaleDateString()}</p>
      {!data.entitled && <p>Automated replies are paused pending payment.</p>}
      {!data.enabled && <p>Razorpay checkout is unavailable. Contact your platform administrator.</p>}
      {subscription && <section aria-label="Subscription" className="space-y-2 rounded border border-border p-3">
        <p>Subscription: {subscription.status}</p>
        {subscription.period_end && <p>Current period ends: {new Date(subscription.period_end).toLocaleDateString()}</p>}
        {subscription.grace_until && <p>Grace period ends: {new Date(subscription.grace_until).toLocaleDateString()}</p>}
        {inCheckout && subscription.checkout_url && <a className="underline" href={subscription.checkout_url}
          target="_blank" rel="noopener noreferrer">Continue to Razorpay checkout</a>}
        {subscription.reconcile_issue && <p role="status">{subscription.reconcile_issue}</p>}
        {subscription.settlement_issue && <p role="status">{subscription.settlement_issue}</p>}
      </section>}
      {data.enabled && <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1">Plan
          <select className={field} value={inCheckout ? subscription.plan : choice} disabled={inCheckout}
            onChange={event => setChoice(event.target.value)}>
            {data.plans.map(item => <option key={item.id} value={item.id} disabled={!item.available}>
              {item.id} · ₹{item.price_inr}/month
            </option>)}
          </select>
        </label>
        {active ? <button className={field} disabled={busy || !plan?.available || choice === subscription.plan}
          onClick={() => void action('billing/subscription', 'PATCH', choice)}>Change plan</button>
          : (!subscription || terminal || inCheckout) && !subscription?.checkout_url &&
            <button className={field} disabled={busy || (!inCheckout && !plan?.available)}
              onClick={() => void action('billing/checkout', 'POST', inCheckout ? subscription.plan : choice)}>
              {inCheckout ? 'Check checkout' : 'Start checkout'}
            </button>}
        {subscription && !terminal && subscription.status !== 'creating' &&
          <button className={field} disabled={busy} onClick={() => {
            if (window.confirm('Cancel this subscription?')) void action('billing/subscription/cancel', 'POST');
          }}>Cancel subscription</button>}
        <button className={field} onClick={() => setRevision(value => value + 1)}>Refresh status</button>
      </div>}
    </>}
  </main>;
}

export default function BillingPage() {
  const workspace = useWorkspace();
  return workspace?.role === 'owner'
    ? <WorkspaceBilling key={workspace.id} id={workspace.id} />
    : <p className="p-4">Only workspace owners can manage billing.</p>;
}
