'use client';

import messages from '@/messages/en.json';
import { UsageMeter } from '@/components/usage-meter';
import { OnboardingChecklist } from '@/components/onboarding-checklist';
import { WidgetSettings } from '@/components/widget-settings';
import { useWorkspace } from '@/components/app-shell';

export default function Dashboard() {
  const active = useWorkspace();
  if (!active) return null;
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.home}</h1>
    <p className="mt-3 text-muted">{messages.onboardingDescription}</p>
    {active.role === 'owner' && <div className="mt-6">
      <UsageMeter key={active.id} workspaceId={active.id} />
    </div>}
    <OnboardingChecklist key={active.id} workspaceId={active.id}
      canConnect={active.role === 'owner' || active.role === 'admin'} />
    {(active.role === 'owner' || active.role === 'admin') &&
      <WidgetSettings key={active.id} workspaceId={active.id} />}
    <section className="mt-8 rounded-xl border border-border p-5">
      <h2 className="text-lg font-semibold">{messages.needsAttention}</h2>
      <p className="mt-3 text-muted">{messages.needsAttentionEmpty}</p>
    </section>
  </main>;
}
