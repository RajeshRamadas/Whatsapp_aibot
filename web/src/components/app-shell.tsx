'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '@/lib/api';
import { visiblePages, type Role } from '@/lib/navigation';
import messages from '@/messages/en.json';

export type Workspace = { id: string; name: string; role: Role };
export function AppShell({ workspaces, active, children }: { workspaces: Workspace[]; active: Workspace; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [selected, setSelected] = useState(active.id);
  const pages = visiblePages(active.role);
  function switchWorkspace(id: string) {
    setSelected(id);
    document.cookie = 'active_workspace=' + encodeURIComponent(id) + '; Path=/; SameSite=Lax';
    router.refresh();
  }
  async function logout() {
    await api('auth/logout', { method: 'POST' });
    router.replace('/login');
    router.refresh();
  }
  return <div className="min-h-screen md:grid md:grid-cols-[240px_minmax(0,1fr)]">
    <aside className="hidden border-r border-border bg-surface p-4 md:block">
      <Link href="/dashboard" className="block text-xl font-semibold">{messages.brand}</Link>
      <label htmlFor="workspace" className="mt-6 block text-sm">{messages.switchWorkspace}</label>
      <select id="workspace" className="mt-2 w-full rounded-lg border border-border bg-surface px-2" value={selected} onChange={event => switchWorkspace(event.target.value)}>
        {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
      </select>
      <nav aria-label={messages.navigation} className="mt-8 grid gap-2">
        {pages.map(page => <Link key={page.key} href={page.path} aria-current={pathname === page.path ? 'page' : undefined}
          className="rounded-lg px-3 py-3 hover:bg-border aria-[current=page]:bg-border">{messages[page.key]}</Link>)}
      </nav>
      <button type="button" className="mt-8 underline" onClick={logout}>{messages.logout}</button>
    </aside>
    <div className="min-w-0 pb-24 md:pb-0">
      <header className="flex items-center justify-between gap-2 border-b border-border p-3 md:hidden">
        <span className="font-semibold">{messages.brand}</span>
        <label className="sr-only" htmlFor="workspace-mobile">{messages.switchWorkspace}</label>
        <select id="workspace-mobile" className="max-w-[60%] rounded-lg border border-border bg-surface px-2" value={selected} onChange={event => switchWorkspace(event.target.value)}>
          {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
        </select>
      </header>
      {children}
    </div>
    <nav aria-label={messages.navigation} className="fixed inset-x-0 bottom-0 z-10 flex gap-1 overflow-x-auto border-t border-border bg-surface p-2 md:hidden">
      {pages.map(page => <Link key={page.key} href={page.path} aria-current={pathname === page.path ? 'page' : undefined}
        className="flex min-h-11 shrink-0 items-center rounded-lg px-3 text-sm aria-[current=page]:bg-border">{messages[page.key]}</Link>)}
    </nav>
  </div>;
}
