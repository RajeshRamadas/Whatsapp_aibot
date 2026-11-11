'use client';
import { useWorkspace } from '@/components/app-shell';
import { OutgoingWebhooks } from '@/components/outgoing-webhooks';
import { ApiReference, Developer } from '@/components/developer';
import { useCopy } from '@/i18n/provider';
export default function DeveloperPage() {
  const copy = useCopy('developer'), workspace = useWorkspace();
  const admin = workspace && ['owner', 'admin'].includes(workspace.role);
  return <main id="main" tabIndex={-1} className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.intro}</p>
    {admin ? <Developer key={workspace.id} workspaceId={workspace.id} /> : <p className="mt-4">{copy.adminOnly}</p>}
    <ApiReference />
    {admin && <OutgoingWebhooks key={workspace.id} workspaceId={workspace.id} />}
  </main>;
}
