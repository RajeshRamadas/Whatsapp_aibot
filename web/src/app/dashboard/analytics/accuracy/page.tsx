'use client';
import Link from 'next/link';
import { useWorkspace } from '@/components/app-shell';
import { Accuracy } from '@/components/accuracy';
import { useCopy } from '@/i18n/provider';
export default function AccuracyPage() {
  const copy = useCopy('accuracy'), workspace = useWorkspace();
  return <main id="main" tabIndex={-1} className="mx-auto max-w-6xl p-4 sm:p-8">
    <Link href="/dashboard/analytics" className="inline-block min-h-11 underline">{copy.back}</Link>
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.intro}</p>
    {workspace && <Accuracy key={workspace.id} workspaceId={workspace.id} admin={['owner', 'admin'].includes(workspace.role)} />}
  </main>;
}
