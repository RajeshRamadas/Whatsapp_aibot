'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { InboxTemplatePicker } from '@/components/inbox-template-picker';
import { InboxMedia, MessageMedia } from '@/components/inbox-media';
import type { Attachment } from '@/api/media';
import { useWorkspace } from '@/components/app-shell';
import { api } from '@/lib/api';

type Conversation = {
  id: string; user: string; channel: string; status: string;
  labels: string[]; can_reply_freely: boolean; assignee_user_id: string | null;
};
type Citation = { number: number; filename: string; heading?: string; page?: number };
type Message = { role: string; kind: string; content: string; delivery_status: string | null; sources?: Citation[]; attachments?: Attachment[] };
type Page = { items: Conversation[]; next_cursor: string | null; counts: Record<string, number> };
type Canned = { id: string; title: string; body: string };
const endpoint = 'inbox/conversations';
const field = 'min-h-11 rounded border border-border bg-surface p-2';

export function Inbox({ canAct }: { canAct: boolean }) {
  const workspace = useWorkspace();
  const [page, setPage] = useState<Page>();
  const [selected, select] = useState('');
  const [linkedConversation, setLinkedConversation] = useState<Conversation>();
  const [lead, setLead] = useState<{ id: string; status: string; fields: Record<string, string> }>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [canned, setCanned] = useState<Canned[]>([]);
  const [status, setStatus] = useState('');
  const [channel, setChannel] = useState('');
  const [assignee, setAssignee] = useState('');
  const [label, setLabel] = useState('');
  const [typing, setTyping] = useState('');
  const composer = useRef<HTMLTextAreaElement>(null);
  const lastTyping = useRef(0);
  const pendingReply = useRef<{ conversation: string; text: string; key: string } | null>(null);
  const [search, setSearch] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, refresh] = useState(0);
  const active = page?.items.find(item => item.id === selected)
    ?? (linkedConversation?.id === selected ? linkedConversation : undefined);
  const draft = drafts[selected] ?? '';

  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get('conversation');
    if (linked) select(linked);
  }, []);

  useEffect(() => {
    const stream = new EventSource('/api/backend/events');
    const invalidate = () => refresh(value => value + 1);
    for (const event of ['conversation.updated', 'message.created', 'message.updated', 'lead.created']) {
      stream.addEventListener(event, invalidate);
    }
    let timeout: ReturnType<typeof setTimeout> | undefined;
    stream.addEventListener('typing.updated', (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as { conversation_id: string; user_id?: string; user_name?: string };
      if (data.conversation_id !== selected) return;
      setTyping(data.user_name ? data.user_name + ' is typing…' : 'An agent is typing…');
      clearTimeout(timeout);
      timeout = setTimeout(() => setTyping(''), 5000);
    });
    return () => { stream.close(); clearTimeout(timeout); };
  }, [selected]);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams();
    if (status) query.set('status', status);
    if (channel) query.set('channel', channel);
    if (search) query.set('search', search);
    if (assignee) query.set('assignee', assignee);
    if (label) query.set('label', label);
    api<Page>(endpoint + '?' + query, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setPage(result); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [status, channel, search, assignee, label, revision]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const id = encodeURIComponent(selected);
    api<Conversation>(endpoint + '/' + id, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setLinkedConversation(result); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    api<{ id: string; status: string; fields: Record<string, string> }>('leads/' + id, {
      signal: controller.signal,
    }).then(result => { if (!controller.signal.aborted) setLead(result); })
      .catch(() => { if (!controller.signal.aborted) setLead(undefined); });
    api<Message[]>('conversations/' + id + '/messages', { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setMessages(result); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [selected, revision]);

  useEffect(() => {
    const controller = new AbortController();
    api<Canned[]>('inbox/canned-replies', { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setCanned(result); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!page || !workspace || !('serviceWorker' in navigator)) return;
    const saveSnapshot = () => navigator.serviceWorker.controller?.postMessage({
      type: 'INBOX_SNAPSHOT', workspace: workspace.name,
      conversations: page.items, messages,
    });
    saveSnapshot();
    navigator.serviceWorker.addEventListener('controllerchange', saveSnapshot);
    return () => navigator.serviceWorker.removeEventListener('controllerchange', saveSnapshot);
  }, [page, messages, workspace]);

  async function action(name: string, payload?: object, key?: string) {
    if (!canAct || busy) return;
    setBusy(true);
    setError('');
    try {
      await api(endpoint + '/' + selected + '/' + name, {
        method: name === 'labels' ? 'PUT' : 'POST',
        headers: key ? { 'Idempotency-Key': key } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      });
      if (name === 'reply') pendingReply.current = null;
      if (name === 'reply' || name === 'note') setDrafts(old => ({ ...old, [selected]: '' }));
      refresh(value => value + 1);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function loadMore() {
    if (!page?.next_cursor) return;
    const query = new URLSearchParams({ cursor: page.next_cursor, status, channel, search, assignee, label });
    try {
      const next = await api<Page>(endpoint + '?' + query);
      setPage(current => current === page ? { ...next, items: [...page.items, ...next.items] } : current);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  function choose(id: string) {
    if (id === selected) return;
    setTyping('');
    setMessages([]);
    select(id);
  }

  return <main id="main" tabIndex={-1} className="p-4" onKeyDown={event => {
    if (event.ctrlKey || event.metaKey || event.altKey || !(event.target instanceof HTMLElement)
      || event.target.closest('input, textarea, select, [contenteditable]')) return;
    if (event.key === 'r' || event.key === '/') {
      if (!active) return;
      event.preventDefault();
      if (event.key === '/') setDrafts(old => ({ ...old, [selected]: '/' }));
      composer.current?.focus();
    } else if (event.key === 'j' || event.key === 'k') {
      const rows = page?.items ?? [];
      const index = rows.findIndex(item => item.id === selected);
      const next = rows[index < 0 ? 0 : index + (event.key === 'j' ? 1 : -1)];
      if (next) choose(next.id);
    } else if (event.key === 'e' && active && active.status !== 'closed' && !busy) {
      void action('close');
    }
  }}>
    <h1 className="mb-4 text-2xl font-semibold">Inbox</h1>
    {error && <p role="alert" className="mb-3">{error}</p>}
    <div className="grid gap-4 lg:grid-cols-[minmax(13rem,1fr)_minmax(0,2fr)_minmax(12rem,1fr)]">
      <section aria-label="Conversation list" className={active ? 'hidden lg:block' : ''}>
        <div className="grid gap-2">
          <input className={field} aria-label="Search conversations" placeholder="Search conversations"
            value={search} onChange={event => setSearch(event.target.value)} />
          <select className={field} aria-label="Status" value={status} onChange={event => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {['waiting', 'agent', 'bot', 'closed'].map(value =>
              <option key={value} value={value}>{value} ({page?.counts[value] ?? 0})</option>)}
          </select>
          <select className={field} aria-label="Channel" value={channel} onChange={event => setChannel(event.target.value)}>
            <option value="">All channels</option><option value="whatsapp">WhatsApp</option><option value="web">Web</option>
          </select>
          <input className={field} aria-label="Assignee" placeholder="Assignee ID or unassigned"
            value={assignee} onChange={event => setAssignee(event.target.value)} />
          <input className={field} aria-label="Label" placeholder="Filter by label"
            value={label} onChange={event => setLabel(event.target.value)} />
          {!page && <p role="status">Loading conversations…</p>}
          {page?.items.length === 0 && <p>No conversations found.</p>}
          {page?.items.map(item => <button key={item.id} type="button" className={field + ' text-left'}
            aria-pressed={selected === item.id} onClick={() => choose(item.id)}>
            <span className="block break-all">{item.user}</span>
            <span className="text-sm">{item.channel} · {item.status}</span>
          </button>)}
          {page?.next_cursor && <button className={field} onClick={loadMore}>Load more</button>}
        </div>
      </section>
      {active ? <>
        <section aria-label="Conversation" className="min-w-0">
          <button className={field + ' mb-2 lg:hidden'} onClick={() => choose('')}>Back to conversations</button>
          <h2 className="break-all text-lg font-semibold">{active.user}</h2>
          {canAct && <div className="my-3 flex flex-wrap gap-2">
            <button className={field} disabled={busy} onClick={() => action('take-over')}>Take over</button>
            <button className={field} disabled={busy} onClick={() => action('hand-back')}>Hand back to bot</button>
            <button className={field} disabled={busy} onClick={() => action(active.status === 'closed' ? 'reopen' : 'close')}>
              {active.status === 'closed' ? 'Reopen' : 'Close'}
            </button>
          </div>}
          <ol aria-label="Messages" aria-live="polite" className="mb-4 grid max-h-[50vh] gap-2 overflow-auto">
            {messages.map((message, index) => <li key={index} className="rounded border border-border p-2">
              <span className="text-sm font-semibold">{message.kind === 'note' ? 'Internal note' : message.role}</span>
              {!message.attachments?.some(item => item.transcript === message.content) &&
                <p className="whitespace-pre-wrap break-words">{message.content}</p>}
              {!!message.attachments?.length && <MessageMedia conversationId={selected} attachments={message.attachments} />}
              {!!message.sources?.length && <ul aria-label="Sources" className="mt-2 text-sm">
                {message.sources.map(source => <li key={source.number}>
                  [{source.number}] {source.filename}{source.heading && ' · ' + source.heading}
                  {source.page != null && ' · p. ' + source.page}
                </li>)}
              </ul>}
              {message.delivery_status && <span className="text-sm">{message.delivery_status}</span>}
            </li>)}
          </ol>
          {canAct && <form className="grid gap-2" onSubmit={event => {
            event.preventDefault();
            if (draft.trim() && active.can_reply_freely && active.status !== 'closed' && !busy) {
              const text = draft.trim();
              if (pendingReply.current?.conversation !== selected || pendingReply.current.text !== text) {
                pendingReply.current = { conversation: selected, text, key: crypto.randomUUID() };
              }
              void action('reply', { message: text }, pendingReply.current.key);
            }
          }}>
            {typing && <p role="status">{typing}</p>}
            <textarea ref={composer} aria-label="Reply or internal note" className={field} value={draft} maxLength={4000}
              onChange={event => {
                const text = event.target.value;
                setDrafts(old => ({ ...old, [selected]: text }));
                if (Date.now() - lastTyping.current >= 3000) {
                  lastTyping.current = Date.now();
                  void api(endpoint + '/' + selected + '/typing', { method: 'POST' }).catch(() => undefined);
                }
              }}
              onKeyDown={event => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }} />
            {draft.startsWith('/') && <ul aria-label="Canned replies">
              {canned.filter(item => item.title.toLowerCase().includes(draft.slice(1).toLowerCase())).map(item =>
                <li key={item.id}><button type="button" className={field}
                  onClick={() => setDrafts(old => ({ ...old, [selected]: item.body }))}>{item.title}</button></li>)}
            </ul>}
            <div className="flex flex-wrap gap-2">
              <button type="submit" className={field} disabled={busy || !draft.trim() || !active.can_reply_freely
                || active.status === 'closed'}>Send reply</button>
              <button type="button" className={field} disabled={busy || !draft.trim()}
                onClick={() => action('note', { text: draft.trim() })}>Add internal note</button>
            </div>
          </form>}
          {canAct && active.channel === 'whatsapp' && active.can_reply_freely && active.status !== 'closed' &&
            <InboxMedia key={selected} conversationId={selected} recipient={active.user} onQueued={() => refresh(value => value + 1)} />}
          {canAct && active.channel === 'whatsapp' && !active.can_reply_freely && active.status !== 'closed' &&
            <InboxTemplatePicker key={selected} conversationId={selected}
              onQueued={() => refresh(value => value + 1)} />}
        </section>
        <aside aria-label="Contact details" className="rounded border border-border p-3">
          <h2 className="font-semibold">Contact</h2>
          <p className="break-all">{active.user}</p>
          <p>{active.channel} · {active.status}</p>
          <p>Labels: {active.labels.join(', ') || 'None'}</p>
          <p className="break-all">Assignee: {active.assignee_user_id ?? 'Unassigned'}</p>
          <h3 className="mt-4 font-semibold">Lead</h3>
          {lead?.id === selected ? <>
            <p className="break-words">{lead.fields.name || lead.fields.phone || 'Unnamed lead'} · {lead.status}</p>
            <Link href="/dashboard/leads" className="underline">View leads</Link>
          </> : <p>No lead captured.</p>}
        </aside>
      </> : <p className="hidden lg:block">Choose a conversation to see its messages.</p>}
    </div>
  </main>;
}
