import Link from 'next/link';
import { cookies } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { PlatformConsole } from '@/components/platform-console';
import copy from '@/messages/platform.en.json';

export default async function PlatformPage() {
  const cookie = (await cookies()).toString();
  const response = await fetch((process.env.API_INTERNAL_URL ?? 'http://localhost:8000') + '/api/v1/platform/me', {
    headers: { cookie }, cache: 'no-store',
  });
  if (response.status === 401) redirect('/login');
  if (response.status === 403) notFound();
  if (!response.ok) throw new Error(copy.error);
  return <main id="main" className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
    <Link href="/dashboard" className="inline-flex min-h-11 items-center underline">{copy.dashboard}</Link>
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="text-muted">{copy.intro}</p>
    <PlatformConsole />
  </main>;
}
