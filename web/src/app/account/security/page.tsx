import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import { AccountSecurity } from '@/components/account-security';
import { catalogFor } from '@/i18n/catalogs';
import { requestLocale } from '@/i18n/server';
export default async function SecurityPage() {
  const jar = await cookies(), copy = catalogFor(await requestLocale()).security;
  const me = await fetch((process.env.API_INTERNAL_URL ?? 'http://localhost:8000') + '/api/v1/auth/me', { headers: { cookie: jar.toString() }, cache: 'no-store' });
  if (!me.ok) redirect('/login');
  return <main id="main" tabIndex={-1} className="mx-auto max-w-3xl p-4 sm:p-8">
    <Link className="inline-block min-h-11 underline" href="/dashboard">{copy.back}</Link>
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.intro}</p>
    <AccountSecurity />
  </main>;
}
