import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import messages from '@/messages/en.json';
export default async function Dashboard() {
  const cookie = (await cookies()).toString();
  const base = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  const response = await fetch(base + '/api/v1/auth/me', { headers: { cookie }, cache: 'no-store' });
  if (!response.ok) redirect('/login');
  const workspaces = await fetch(base + '/api/v1/workspaces', { headers: { cookie }, cache: 'no-store' });
  if (!workspaces.ok) redirect('/login');
  const data = await workspaces.json() as { id: string; name: string }[];
  if (!data.length) redirect('/create-workspace');
  return <main id="main" className="mx-auto max-w-4xl p-6">
    <h1 className="text-2xl font-semibold">{messages.dashboard}</h1>
    <ul className="mt-5 grid gap-3">{data.map(item => <li key={item.id} className="rounded-xl border border-border p-4">{item.name}</li>)}</ul>
    <Link href="/create-workspace" className="mt-6 inline-block underline">{messages.workspace}</Link>
  </main>;
}
