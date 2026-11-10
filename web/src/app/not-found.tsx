import Link from 'next/link';
import { getCopy } from '@/i18n/server';
export default async function NotFound() {
  const copy = await getCopy('errors');
  return <main id="main" className="mx-auto max-w-2xl p-8">
    <h1 className="text-2xl font-semibold">{copy.notFound}</h1>
    <p className="my-4">{copy.notFoundHelp}</p>
    <Link className="inline-flex min-h-11 items-center underline" href="/dashboard">{copy.home}</Link>
  </main>;
}
