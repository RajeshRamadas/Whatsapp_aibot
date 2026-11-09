'use client';
import { AnalyticsOverview } from '@/components/analytics-overview';
import { useWorkspace } from '@/components/app-shell';
import copy from '@/messages/analytics.en.json';

export default function AnalyticsPage() {
  const active = useWorkspace();
  if (!active) return null;
  return <main id="main" className="mx-auto max-w-6xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.description}</p>
    <AnalyticsOverview key={active.id} workspaceId={active.id} />
  </main>;
}
