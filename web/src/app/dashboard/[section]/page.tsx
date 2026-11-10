import { getCopy } from '@/i18n/server';
import { notFound } from 'next/navigation';
import { pages } from '@/lib/navigation';

export default async function Section({ params }: { params: Promise<{ section: string }> }) {
  const messages = await getCopy('base');
  const { section } = await params;
  const page = pages.find(item => item.path === '/dashboard/' + section);
  if (!page) notFound();
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages[page.key]}</h1>
    <div className="mt-6 rounded-xl border border-border bg-surface p-6">
      <p>{messages.sectionEmpty}</p>
    </div>
  </main>;
}
