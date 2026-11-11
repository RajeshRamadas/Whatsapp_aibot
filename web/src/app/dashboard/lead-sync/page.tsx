'use client';
import { useWorkspace } from '@/components/app-shell';
import { LeadSync } from '@/components/lead-sync';
import { useCopy } from '@/i18n/provider';
export default function LeadSyncPage() {
  const workspace = useWorkspace(), copy = useCopy('leadSync');
  return <main id="main" tabIndex={-1} className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.intro}</p>
    {workspace && (['owner', 'admin'].includes(workspace.role) ? <LeadSync key={workspace.id} workspaceId={workspace.id} /> : <p className="mt-5">{copy.denied}</p>)}
  </main>;
}
