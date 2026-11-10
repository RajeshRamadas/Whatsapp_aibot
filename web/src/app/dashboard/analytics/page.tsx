'use client';
import Link from 'next/link';
import improvements from '@/messages/improvements.en.json';
import { AnalyticsOverview } from '@/components/analytics-overview';
import { useWorkspace } from '@/components/app-shell';
import copy from '@/messages/analytics.en.json';

export default function AnalyticsPage() {
  const active = useWorkspace();
  if (!active) return null;
  return <main id="main" className="mx-auto max-w-6xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.description}</p>
    {(active.role === 'owner' || active.role === 'admin') && <Link href="/dashboard/knowledge?view=improve"
      className="mt-4 inline-block py-2 underline">{improvements.analyticsLink}</Link>}
    <AnalyticsOverview key={active.id} workspaceId={active.id} />
  </main>;
}
