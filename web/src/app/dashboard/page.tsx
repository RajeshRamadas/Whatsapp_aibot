import { cookies } from 'next/headers';
import messages from '@/messages/en.json';
import { OnboardingChecklist } from '@/components/onboarding-checklist';
import type { Workspace } from '@/components/app-shell';
export default async function Dashboard() {
  const jar = await cookies();
  const cookie = jar.toString();
  const base = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  const response = await fetch(base + '/api/v1/workspaces', { headers: { cookie }, cache: 'no-store' });
  const workspaces = await response.json() as Workspace[];
  const active = workspaces.find(item => item.id === jar.get('active_workspace')?.value) ?? workspaces[0];
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.home}</h1>
    <p className="mt-3 text-muted">{messages.onboardingDescription}</p>
    <OnboardingChecklist workspaceId={active.id} />
    <section className="mt-8 rounded-xl border border-border p-5">
      <h2 className="text-lg font-semibold">{messages.needsAttention}</h2>
      <p className="mt-3 text-muted">{messages.needsAttentionEmpty}</p>
    </section>
  </main>;
}
