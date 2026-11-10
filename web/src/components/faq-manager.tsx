'use client';
import { useCopy } from '@/i18n/provider';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';


type Pair = { id: string; question: string; answer: string };
const endpoint = 'qa-pairs';
const pageSize = 25;
const field = 'min-h-11 rounded border border-border bg-surface px-3 py-2';

export function FaqManager({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('faq');
  const [rows, setRows] = useState<Pair[]>();
  const [draft, setDraft] = useState<Pair>({ id: '', question: '', answer: '' });
  const [offset, setOffset] = useState(0);
  const [version, refresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const headers = { 'X-Workspace-Id': workspaceId };

  useEffect(() => {
    const controller = new AbortController();
    api<Pair[]>(endpoint + '?limit=' + (pageSize + 1) + '&offset=' + offset, {
      signal: controller.signal, headers: { 'X-Workspace-Id': workspaceId },
    }).then(result => { if (!controller.signal.aborted) setRows(result); })
      .catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => controller.abort();
  }, [workspaceId, offset, version, copy.loadError]);

  function clearDraft() {
    setDraft({ id: '', question: '', answer: '' });
  }

  async function change(pair?: Pair) {
    if (busy || (pair && !window.confirm(copy.confirmDelete))) return;
    setBusy(true);
    setError('');
    try {
      if (pair) {
        await api(endpoint + '/' + pair.id, { method: 'DELETE', headers });
        if (pair.id === draft.id) clearDraft();
        if (rows?.length === 1 && offset) setOffset(offset - pageSize);
      } else {
        const { id, question, answer } = draft;
        await api(endpoint + (id ? '/' + id : ''), {
          method: id ? 'PUT' : 'POST', headers, body: JSON.stringify({ question, answer }),
        });
        clearDraft();
      }
      refresh(value => value + 1);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.saveError);
    } finally {
      setBusy(false);
    }
  }

  return <section className="space-y-4" aria-label={copy.title}>
    <h2 className="text-lg font-semibold">{copy.title}</h2>
    <p>{copy.description}</p>
    <form className="grid gap-2" onSubmit={event => { event.preventDefault(); void change(); }}>
      <label className="grid gap-1">{copy.question}
        <input className={field} required maxLength={1000} value={draft.question}
          onChange={event => setDraft({ ...draft, question: event.target.value })} />
      </label>
      <label className="grid gap-1">{copy.answer}
        <textarea className={field} required maxLength={6000} value={draft.answer}
          onChange={event => setDraft({ ...draft, answer: event.target.value })} />
      </label>
      <div className="flex flex-wrap gap-2">
        <button className={field} disabled={busy || !draft.question.trim() || !draft.answer.trim()}>
          {draft.id ? copy.save : copy.add}
        </button>
        {draft.id && <button type="button" className={field} disabled={busy} onClick={clearDraft}>
          {copy.cancel}
        </button>}
      </div>
    </form>
    {error && <p role="alert">{error}</p>}
    {!rows && !error && <p role="status">{copy.loading}</p>}
    {rows?.length === 0 && <p>{copy.empty}</p>}
    <ul className="grid gap-3">
      {rows?.slice(0, pageSize).map(pair => <li key={pair.id} className="rounded border border-border p-3">
        <h3 className="break-words font-semibold">{pair.question}</h3>
        <p className="whitespace-pre-wrap break-words">{pair.answer}</p>
        <div className="mt-2 flex gap-2">
          <button className={field} disabled={busy} onClick={() => setDraft(pair)}>{copy.edit}</button>
          <button className={field} disabled={busy} onClick={() => void change(pair)}>{copy.remove}</button>
        </div>
      </li>)}
    </ul>
    <div className="flex gap-2">
      {offset > 0 && <button className={field} onClick={() => setOffset(offset - pageSize)}>{copy.previous}</button>}
      {(rows?.length ?? 0) > pageSize &&
        <button className={field} onClick={() => setOffset(offset + pageSize)}>{copy.next}</button>}
    </div>
  </section>;
}
