'use client';
import { useCopy } from '@/i18n/provider';
import { useEffect, useState } from 'react';
import { WhatsAppSignup } from '@/components/whatsapp-signup';
import { api } from '@/lib/api';

type Steps = { connect_whatsapp: boolean; add_knowledge: boolean; test_bot: boolean; go_live: boolean };
const keys = ['connect_whatsapp', 'add_knowledge', 'test_bot', 'go_live'] as const;
export function OnboardingChecklist({ workspaceId, canConnect }: { workspaceId: string; canConnect: boolean }) {
  const messages = useCopy('base');
  const [steps, setSteps] = useState<Steps | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    api<Steps>('onboarding', { headers: { 'X-Workspace-Id': workspaceId } })
      .then(data => { if (active) setSteps(data); })
      .catch(() => { if (active) setError(messages.genericError); });
    return () => { active = false; };
  }, [workspaceId, reload, messages.genericError]);
  return <section className="mt-8" aria-labelledby="checklist-heading">
    <h2 id="checklist-heading" className="text-xl font-semibold">{messages.onboarding}</h2>
    {error && <div role="alert"><p>{error}</p><button type="button" className="underline" onClick={() => { setError(''); setReload(n => n + 1); }}>{messages.retry}</button></div>}
    {!steps && !error && <p role="status">{messages.loading}</p>}
    {steps && <ul className="mt-4 grid gap-3">
      {keys.map(key => <li key={key} className="flex items-center gap-3 rounded-xl border border-border p-4">
        <span aria-hidden="true">{steps[key] ? '✓' : '○'}</span>
        <span>{messages[key]}</span>
        <span className="ml-auto text-sm text-muted">{steps[key] ? messages.complete : messages.pending}</span>
      </li>)}
    </ul>}
    {canConnect && <WhatsAppSignup workspaceId={workspaceId} onConnected={() => setReload(n => n + 1)} />}
  </section>;
}
