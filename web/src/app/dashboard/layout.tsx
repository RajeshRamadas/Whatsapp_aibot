import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppShell, type Workspace } from '@/components/app-shell';
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  const cookie = jar.toString();
  const base = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  const me = await fetch(base + '/api/v1/auth/me', { headers: { cookie }, cache: 'no-store' });
  if (!me.ok) redirect('/login');
  const response = await fetch(base + '/api/v1/workspaces', { headers: { cookie }, cache: 'no-store' });
  if (!response.ok) redirect('/login');
  const workspaces = await response.json() as Workspace[];
  if (!workspaces.length) redirect('/create-workspace');
  const selected = jar.get('active_workspace')?.value;
  const active = workspaces.find(item => item.id === selected) ?? workspaces[0];
  return <AppShell workspaces={workspaces} active={active}>{children}</AppShell>;
}
