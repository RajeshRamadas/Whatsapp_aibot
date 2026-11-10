import { getCopy } from '@/i18n/server';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import type { Workspace } from '@/components/app-shell';
import { BotSettings } from '@/components/bot-settings';

export default async function BotPage() {
  const messages = await getCopy('base');
  const jar = await cookies();
  const response = await fetch((process.env.API_INTERNAL_URL ?? 'http://localhost:8000') + '/api/v1/workspaces',
    { headers: { cookie: jar.toString() }, cache: 'no-store' });
  if (!response.ok) redirect('/login');
  const rows = await response.json() as (Omit<Workspace, 'id'> & { tenant_id: string })[];
  const workspaces = rows.map(row => ({ ...row, id: row.tenant_id }));
  const active = workspaces.find(item => item.id === jar.get('active_workspace')?.value) ?? workspaces[0];
  if (!active) redirect('/create-workspace');
  if (!['owner', 'admin'].includes(active.role)) redirect('/dashboard');
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.botSettings}</h1>
    <p className="mt-2 text-muted">{messages.botSettingsDescription}</p>
    <BotSettings workspaceId={active.id} />
  </main>;
}
