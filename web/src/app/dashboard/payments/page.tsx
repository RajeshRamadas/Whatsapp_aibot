'use client';
import { useWorkspace } from '@/components/app-shell';
import { Payments } from '@/components/payments';
import { useCopy } from '@/i18n/provider';
export default function PaymentPage() {
  const copy = useCopy('payments'), workspace = useWorkspace();
  return <main id="main" tabIndex={-1} className="mx-auto max-w-6xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{copy.title}</h1><p className="mt-3 text-muted">{copy.intro}</p>
    {workspace && (workspace.role === 'viewer' ? <p className="mt-5">{copy.denied}</p>
      : <Payments key={workspace.id} workspaceId={workspace.id} admin={['owner', 'admin'].includes(workspace.role)} />)}
  </main>;
}
