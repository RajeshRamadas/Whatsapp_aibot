'use client';
import { useWorkspace } from '@/components/app-shell';
import { Campaigns } from '@/components/campaigns';
import copy from '@/messages/campaigns.en.json';
export default function CampaignPage() {
  const workspace = useWorkspace();
  return <main id="main" className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="text-muted">{copy.description}</p>
    {workspace && (['owner', 'admin'].includes(workspace.role)
      ? <Campaigns key={workspace.id} workspaceId={workspace.id} /> : <p>{copy.denied}</p>)}
  </main>;
}
