import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppShell, type Workspace } from '@/components/app-shell';
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  const cookie = jar.toString();
  const base = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  const me = await fetch(base + '/api/v1/auth/me', { headers: { cookie }, cache: 'no-store' });
  if (!me.ok) redirect('/login');
  const user = await me.json() as { is_platform_admin: boolean };
  const response = await fetch(base + '/api/v1/workspaces', { headers: { cookie }, cache: 'no-store' });
  if (!response.ok) redirect('/login');
  const rows = await response.json() as (Omit<Workspace, 'id'> & { tenant_id: string })[];
  const workspaces = rows.map(row => ({ ...row, id: row.tenant_id }));
  if (!workspaces.length) redirect(user.is_platform_admin ? '/platform' : '/create-workspace');
  const selected = jar.get('active_workspace')?.value;
  const active = workspaces.find(item => item.id === selected) ?? workspaces[0];
  return <AppShell workspaces={workspaces} active={active} platformAdmin={user.is_platform_admin}>{children}</AppShell>;
}
