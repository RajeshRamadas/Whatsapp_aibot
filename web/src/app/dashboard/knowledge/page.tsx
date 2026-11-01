import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { KnowledgeManager } from '@/components/knowledge-manager';
import type { Workspace } from '@/components/app-shell';
import messages from '@/messages/en.json';
export default async function Knowledge() {
  const jar = await cookies();
  const cookie = jar.toString();
  const base = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  const response = await fetch(base + '/api/v1/workspaces', { headers: { cookie }, cache: 'no-store' });
  if (!response.ok) redirect('/login');
  const workspaces = await response.json() as Workspace[];
  const active = workspaces.find(item => item.id === jar.get('active_workspace')?.value) ?? workspaces[0];
  if (!active) redirect('/create-workspace');
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.knowledge}</h1>
    <p className="mt-3 text-muted">{messages.knowledgeDescription}</p>
    {active.role === 'viewer' || active.role === 'agent'
      ? <p className="mt-3 text-muted">{messages.knowledgeAdminOnly}</p>
      : <KnowledgeManager workspaceId={active.id} canEdit />}
  </main>;
}
