'use client';
import { useCopy } from '@/i18n/provider';
import Link from 'next/link';

import { AnalyticsOverview } from '@/components/analytics-overview';
import { useWorkspace } from '@/components/app-shell';


export default function AnalyticsPage() {
  const improvements = useCopy('improvements');
  const copy = useCopy('analytics');
  const active = useWorkspace();
  if (!active) return null;
  return <main id="main" className="mx-auto max-w-6xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.description}</p>
    {(active.role === 'owner' || active.role === 'admin') && <Link href="/dashboard/knowledge?view=improve"
      className="mt-4 inline-block py-2 underline">{improvements.analyticsLink}</Link>}
    <AnalyticsOverview key={active.id} workspaceId={active.id} />
  </main>;
}
