'use client';

import Link from 'next/link';
import { useCopy, useLocale } from '@/i18n/provider';
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
  const copy = useCopy('inbox');
  const locale = useLocale();
  const labelFor = (value: string) => (copy as Record<string, string>)[value] ?? value;
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
      setTyping(data.user_name || '\0');
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
      type: 'INBOX_SNAPSHOT', workspace: workspace.name, locale,
      conversations: page.items, messages,
    });
    saveSnapshot();
    navigator.serviceWorker.addEventListener('controllerchange', saveSnapshot);
    return () => navigator.serviceWorker.removeEventListener('controllerchange', saveSnapshot);
  }, [page, messages, workspace, locale]);

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
    <h1 className="mb-4 text-2xl font-semibold">{copy.title}</h1>
    {error && <p role="alert" className="mb-3">{error}</p>}
    <div className="grid gap-4 lg:grid-cols-[minmax(13rem,1fr)_minmax(0,2fr)_minmax(12rem,1fr)]">
      <section aria-label={copy.conversationList} className={active ? 'hidden lg:block' : ''}>
        <div className="grid gap-2">
          <input className={field} aria-label={copy.search} placeholder={copy.search}
            value={search} onChange={event => setSearch(event.target.value)} />
          <select className={field} aria-label={copy.status} value={status} onChange={event => setStatus(event.target.value)}>
            <option value="">{copy.allStatuses}</option>
            {['waiting', 'agent', 'bot', 'closed'].map(value =>
              <option key={value} value={value}>{labelFor(value)} ({page?.counts[value] ?? 0})</option>)}
          </select>
          <select className={field} aria-label={copy.channel} value={channel} onChange={event => setChannel(event.target.value)}>
            <option value="">{copy.allChannels}</option><option value="whatsapp">{copy.whatsapp}</option><option value="web">{copy.web}</option>
          </select>
          <input className={field} aria-label={copy.assignee} placeholder={copy.assigneeHint}
            value={assignee} onChange={event => setAssignee(event.target.value)} />
          <input className={field} aria-label={copy.label} placeholder={copy.filterLabel}
            value={label} onChange={event => setLabel(event.target.value)} />
          {!page && <p role="status">{copy.loading}</p>}
          {page?.items.length === 0 && <p>{copy.empty}</p>}
          {page?.items.map(item => <button key={item.id} type="button" className={field + ' text-left'}
            aria-pressed={selected === item.id} onClick={() => choose(item.id)}>
            <span className="block break-all">{item.user}</span>
            <span className="text-sm">{labelFor(item.channel)} · {labelFor(item.status)}</span>
          </button>)}
          {page?.next_cursor && <button className={field} onClick={loadMore}>{copy.loadMore}</button>}
        </div>
      </section>
      {active ? <>
        <section aria-label={copy.conversation} className="min-w-0">
          <button className={field + ' mb-2 lg:hidden'} onClick={() => choose('')}>{copy.back}</button>
          <h2 className="break-all text-lg font-semibold">{active.user}</h2>
          {canAct && <div className="my-3 flex flex-wrap gap-2">
            <button className={field} disabled={busy} onClick={() => action('take-over')}>{copy.takeOver}</button>
            <button className={field} disabled={busy} onClick={() => action('hand-back')}>{copy.handBack}</button>
            <button className={field} disabled={busy} onClick={() => action(active.status === 'closed' ? 'reopen' : 'close')}>
              {active.status === 'closed' ? copy.reopen : copy.close}
            </button>
          </div>}
          <ol aria-label={copy.messages} aria-live="polite" className="mb-4 grid max-h-[50vh] gap-2 overflow-auto">
            {messages.map((message, index) => <li key={index} className="rounded border border-border p-2">
              <span className="text-sm font-semibold">{message.kind === 'note' ? copy.internalNote : labelFor(message.role)}</span>
              {!message.attachments?.some(item => item.transcript === message.content) &&
                <p className="whitespace-pre-wrap break-words">{message.content}</p>}
              {!!message.attachments?.length && <MessageMedia conversationId={selected} attachments={message.attachments} />}
              {!!message.sources?.length && <ul aria-label={copy.sources} className="mt-2 text-sm">
                {message.sources.map(source => <li key={source.number}>
                  [{source.number}] {source.filename}{source.heading && ' · ' + source.heading}
                  {source.page != null && ' · ' + copy.page + ' ' + source.page}
                </li>)}
              </ul>}
              {message.delivery_status && <span className="text-sm">{labelFor(message.delivery_status)}</span>}
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
            {typing && <p role="status">{typing === '\0' ? copy.agentTyping : copy.typing.replace('{name}', typing)}</p>}
            <textarea ref={composer} aria-label={copy.composer} className={field} value={draft} maxLength={4000}
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
            {draft.startsWith('/') && <ul aria-label={copy.canned}>
              {canned.filter(item => item.title.toLowerCase().includes(draft.slice(1).toLowerCase())).map(item =>
                <li key={item.id}><button type="button" className={field}
                  onClick={() => setDrafts(old => ({ ...old, [selected]: item.body }))}>{item.title}</button></li>)}
            </ul>}
            <div className="flex flex-wrap gap-2">
              <button type="submit" className={field} disabled={busy || !draft.trim() || !active.can_reply_freely
                || active.status === 'closed'}>{copy.send}</button>
              <button type="button" className={field} disabled={busy || !draft.trim()}
                onClick={() => action('note', { text: draft.trim() })}>{copy.addNote}</button>
            </div>
          </form>}
          {canAct && active.channel === 'whatsapp' && active.can_reply_freely && active.status !== 'closed' &&
            <InboxMedia key={selected} conversationId={selected} recipient={active.user} onQueued={() => refresh(value => value + 1)} />}
          {canAct && active.channel === 'whatsapp' && !active.can_reply_freely && active.status !== 'closed' &&
            <InboxTemplatePicker key={selected} conversationId={selected}
              onQueued={() => refresh(value => value + 1)} />}
        </section>
        <aside aria-label={copy.contactDetails} className="rounded border border-border p-3">
          <h2 className="font-semibold">{copy.contact}</h2>
          <p className="break-all">{active.user}</p>
          <p>{labelFor(active.channel)} · {labelFor(active.status)}</p>
          <p>{copy.labels}: {active.labels.join(', ') || copy.none}</p>
          <p className="break-all">{copy.assignee}: {active.assignee_user_id ?? copy.unassigned}</p>
          <h3 className="mt-4 font-semibold">{copy.lead}</h3>
          {lead?.id === selected ? <>
            <p className="break-words">{lead.fields.name || lead.fields.phone || copy.unnamedLead} · {lead.status}</p>
            <Link href="/dashboard/leads" className="underline">{copy.viewLeads}</Link>
          </> : <p>{copy.noLead}</p>}
        </aside>
      </> : <p className="hidden lg:block">{copy.choose}</p>}
    </div>
  </main>;
}
