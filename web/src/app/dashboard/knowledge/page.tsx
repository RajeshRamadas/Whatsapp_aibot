'use client';

import { useState } from 'react';
import { useWorkspace, type Workspace } from '@/components/app-shell';
import { FaqManager } from '@/components/faq-manager';
import { KnowledgeManager } from '@/components/knowledge-manager';
import messages from '@/messages/en.json';
import faq from '@/messages/faq.en.json';

function WorkspaceKnowledge({ workspace }: { workspace: Workspace }) {
  const [showFaq, setShowFaq] = useState(false);
  const canEdit = workspace.role === 'admin' || workspace.role === 'owner';
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.knowledge}</h1>
    <p className="mt-3 text-muted">{messages.knowledgeDescription}</p>
    {canEdit ? <>
      <div className="my-4 flex gap-2" aria-label={messages.knowledge}>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={!showFaq} onClick={() => setShowFaq(false)}>{faq.documents}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={showFaq} onClick={() => setShowFaq(true)}>{faq.title}</button>
      </div>
      {showFaq ? <FaqManager workspaceId={workspace.id} />
        : <KnowledgeManager workspaceId={workspace.id} canEdit />}
    </> : <p className="mt-3 text-muted">{messages.knowledgeAdminOnly}</p>}
  </main>;
}

export default function Knowledge() {
  const workspace = useWorkspace();
  return workspace ? <WorkspaceKnowledge key={workspace.id} workspace={workspace} /> : null;
}
