'use client';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import * as reviews from '@/api/improvements';
import { Button } from '@/components/ui/button';
import { Badge, Card } from '@/components/ui/primitives';


const field = 'min-h-11 w-full rounded-lg border border-border bg-surface p-2';


export function ImproveAnswers({ workspaceId }: { workspaceId: string }) {
  const displayLocale = useDisplayLocale();
  const date = new Intl.DateTimeFormat(displayLocale, { timeZone: 'Asia/Calcutta', dateStyle: 'medium', timeStyle: 'short' });
  const number = new Intl.NumberFormat(displayLocale, { maximumFractionDigits: 1 });
  const copy = useCopy('improvements');
  const [draftFilters, setDraftFilters] = useState<reviews.Filters>(() => ({
    from_date: new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10),
    to_date: new Date().toISOString().slice(0, 10), channel: '', state: 'open', reason: 'attention',
  }));
  const [filters, setFilters] = useState(draftFilters);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<reviews.ReviewList>();
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState('');
  const [draft, setDraft] = useState<reviews.Correction>({ question: '', answer: '' });
  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await reviews.list(workspaceId, filters, offset, signal);
    if (!signal?.aborted) { setData(result); setLoadError(false); }
  }, [workspaceId, filters, offset]);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => { void load(controller.signal).catch(() => {
      if (!controller.signal.aborted) setLoadError(true);
    }); };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [load]);
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : copy.error); }
    finally { setBusy(false); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    void mutate(async () => { await reviews.correct(workspaceId, editor, draft); setEditor(''); });
  }
  function edit(row: reviews.Review) {
    setEditor(row.id); setError('');
    setDraft({ question: (row.draft_question || row.question).slice(0, 1000), answer: row.draft_answer || '' });
  }
  function refresh() { void load().catch(() => setLoadError(true)); }
  return <section className="mt-6 space-y-5" aria-label={copy.title}>
    <h2 className="text-xl font-semibold">{copy.title}</h2><p>{copy.intro}</p>
    <p className="text-sm text-muted">{copy.coverage}</p>
    <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={event => {
      event.preventDefault(); setData(undefined); setOffset(0); setEditor(''); setFilters({ ...draftFilters });
    }}>
      {(['from_date', 'to_date'] as const).map(key => <label key={key}>{key === 'from_date' ? copy.from : copy.to}
        <input className={field} type="date" required value={draftFilters[key]}
          onChange={event => setDraftFilters({ ...draftFilters, [key]: event.target.value })} /></label>)}
      <label>{copy.channel}<select className={field} value={draftFilters.channel}
        onChange={event => setDraftFilters({ ...draftFilters, channel: event.target.value as reviews.Filters['channel'] })}>
        <option value="">{copy.allChannels}</option><option value="web">{copy.web}</option><option value="whatsapp">{copy.whatsapp}</option>
      </select></label>
      {(['state', 'reason'] as const).map(key => <label key={key}>{copy[key]}<select className={field} value={draftFilters[key]}
        onChange={event => setDraftFilters({ ...draftFilters, [key]: event.target.value })}>
        {(key === 'state' ? ['open', 'resolved', 'dismissed', 'all'] as const : ['attention', 'unanswered', 'down', 'all'] as const)
          .map(value => <option key={value} value={value}>{copy[value]}</option>)}
      </select></label>)}
      <div className="flex flex-wrap items-end gap-2"><Button>{copy.apply}</Button>
        <Button type="button" variant="secondary" onClick={refresh}>{copy.refresh}</Button></div>
    </form>
    {loadError && <div role="alert"><p>{copy.error}</p><Button variant="secondary" onClick={refresh}>{copy.retry}</Button></div>}
    {error && <p role="alert">{error}</p>}
    {!data && !loadError && <p role="status">{copy.loading}</p>}
    {data && <>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{(['questions', 'unanswered', 'positive', 'negative', 'satisfaction'] as const)
        .map(key => <Card key={key}><dt className="text-sm text-muted">{copy[key]}</dt>
          <dd className="mt-2 text-2xl font-semibold">{data.summary[key] === null ? copy.noVotes
            : number.format(data.summary[key]!) + (key === 'satisfaction' ? '%' : '')}</dd></Card>)}</dl>
      <p className="text-sm text-muted">{copy.definitions}</p>
      <section className="space-y-2" aria-label={copy.top}><h3 className="text-lg font-semibold">{copy.top}</h3>
        <p className="text-sm text-muted">{copy.topHelp}</p>
        {!data.summary.top_questions.length ? <p>{copy.noTop}</p> : <ol className="space-y-2">{data.summary.top_questions.map((row, i) =>
          <li key={i} className="flex items-start justify-between gap-3 rounded border border-border p-3">
            <span className="min-w-0 break-words">{row.question}</span><span className="shrink-0">{copy.count}: {row.count}</span></li>)}</ol>}
      </section>
      {!data.items.length && <p role="status">{copy.empty}</p>}
      <ul className="space-y-4">{data.items.map(row => {
        const pending = row.state === 'queued' || row.state === 'processing';
        return <li key={row.id} className="space-y-3 rounded-xl border border-border p-4">
          <h3 className="break-words font-semibold">{row.question}</h3>
          <div className="flex flex-wrap gap-2"><Badge tone={row.state === 'resolved' ? 'success' : row.state === 'failed' ? 'danger' : 'neutral'}>
            {copy[row.state]}</Badge>{row.unanswered && <Badge tone="warning">{copy.unanswered}</Badge>}
            <Badge tone={row.feedback === 'down' ? 'danger' : 'neutral'}>{row.feedback ? copy[row.feedback] : copy.noFeedback}</Badge></div>
          <p className="text-sm text-muted">{row.channel === 'web' ? copy.web : copy.whatsapp} · {date.format(new Date(row.created_at))}</p>
          <details><summary className="min-h-11 cursor-pointer py-2">{copy.bot}</summary>
            <p className="whitespace-pre-wrap break-words">{row.answer}</p>
            <p className="mt-2 break-all text-sm">{copy.sources}: {row.sources.map(source => String(source.filename)).join(', ') || copy.noSources}</p>
          </details>
          {row.error && <p role="alert">{row.error}</p>}{row.state === 'resolved' && <p>{copy.saved}</p>}
          <div className="flex flex-wrap gap-2"><Button disabled={busy || pending} onClick={() => edit(row)} aria-expanded={editor === row.id}>
            {row.state === 'resolved' ? copy.edit : row.state === 'failed' ? copy.retryAnswer : copy.add}</Button>
            <Button variant="secondary" disabled={busy || pending} onClick={() => void mutate(() =>
              reviews.resolve(workspaceId, row.id, row.state === 'dismissed' ? 'open' : 'dismissed'))}>
              {row.state === 'dismissed' ? copy.reopen : copy.dismiss}</Button></div>
          {editor === row.id && <form onSubmit={save} className="space-y-3 rounded-lg border border-border p-3">
            <p className="text-sm">{copy.draftHelp}</p>
            <label className="block" htmlFor={'review-question-' + row.id}>{copy.question}</label>
            <textarea id={'review-question-' + row.id} className={field} required maxLength={1000} value={draft.question}
              onChange={event => setDraft({ ...draft, question: event.target.value })} />
            <label className="block" htmlFor={'review-answer-' + row.id}>{copy.answer}</label>
            <textarea id={'review-answer-' + row.id} className={field} required maxLength={6000} rows={4} value={draft.answer}
              onChange={event => setDraft({ ...draft, answer: event.target.value })} />
            <div className="flex flex-wrap gap-2"><Button disabled={busy || !draft.question.trim() || !draft.answer.trim()}>
              {busy ? copy.saving : copy.save}</Button><Button type="button" variant="secondary" onClick={() => setEditor('')}>{copy.cancel}</Button></div>
          </form>}
        </li>;
      })}</ul>
      <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={!offset} onClick={() => {
        setData(undefined); setOffset(Math.max(0, offset - data.limit)); setEditor('');
      }}>{copy.previous}</Button><Button variant="secondary" disabled={offset + data.limit >= data.total} onClick={() => {
        setData(undefined); setOffset(offset + data.limit); setEditor('');
      }}>{copy.next}</Button></div>
    </>}
  </section>;
}
