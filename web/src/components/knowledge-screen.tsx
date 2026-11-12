'use client';
import { useCopy } from '@/i18n/provider';

import { useState } from 'react';
import { useWorkspace, type Workspace } from '@/components/app-shell';
import { FaqManager } from '@/components/faq-manager';
import { WebsiteSources } from '@/components/website-sources';

import { ImproveAnswers } from '@/components/improve-answers';

import { SpreadsheetTables } from '@/components/spreadsheet-tables';
import { CommerceStores } from '@/components/commerce-stores';
import { KnowledgeSync } from '@/components/knowledge-sync';
import { KnowledgeManager } from '@/components/knowledge-manager';



function WorkspaceKnowledge({ workspace, initialView }: { workspace: Workspace; initialView: string }) {
  const websites = useCopy('websites');
  const improvements = useCopy('improvements');
  const messages = useCopy('base');
  const faq = useCopy('faq');
  const sheets = useCopy('spreadsheets');
  const commerce = useCopy('commerce');
  const sync = useCopy('knowledgeSync');
  const [tab, setTab] = useState(['improve', 'tables', 'commerce', 'sync'].includes(initialView) ? initialView : 'documents');
  const canEdit = workspace.role === 'admin' || workspace.role === 'owner';
  return <main id="main" className="mx-auto max-w-5xl p-4 sm:p-8">
    <h1 className="text-2xl font-semibold">{messages.knowledge}</h1>
    <p className="mt-3 text-muted">{messages.knowledgeDescription}</p>
    {canEdit ? <>
      <div className="my-4 flex flex-wrap gap-2" aria-label={messages.knowledge}>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'documents'} onClick={() => setTab('documents')}>{faq.documents}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'sync'} onClick={() => setTab('sync')}>{sync.title}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'commerce'} onClick={() => setTab('commerce')}>{commerce.title}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'tables'} onClick={() => setTab('tables')}>{sheets.title}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'faq'} onClick={() => setTab('faq')}>{faq.title}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'websites'} onClick={() => setTab('websites')}>{websites.title}</button>
        <button className="min-h-11 rounded border border-border px-3 aria-pressed:bg-border"
          aria-pressed={tab === 'improve'} onClick={() => setTab('improve')}>{improvements.title}</button>
      </div>
      {tab === 'sync' ? <KnowledgeSync workspaceId={workspace.id} /> : tab === 'commerce' ? <CommerceStores workspaceId={workspace.id} /> : tab === 'tables' ? <SpreadsheetTables workspaceId={workspace.id} /> : tab === 'improve' ? <ImproveAnswers workspaceId={workspace.id} /> : tab === 'websites' ? <WebsiteSources workspaceId={workspace.id} /> : tab === 'faq' ? <FaqManager workspaceId={workspace.id} />
        : <KnowledgeManager workspaceId={workspace.id} canEdit />}
    </> : <p className="mt-3 text-muted">{messages.knowledgeAdminOnly}</p>}
  </main>;
}

export default function KnowledgeScreen({ initialView }: { initialView: string }) {
  const workspace = useWorkspace();
  return workspace ? <WorkspaceKnowledge key={workspace.id} workspace={workspace} initialView={initialView} /> : null;
}
