'use client';
import { useEffect, useRef, useState } from 'react';
import type { AccuracyCase, AccuracyClient, Act } from '@/api/accuracy';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
function Editor({ value, api, act, busy, done }: { value?: AccuracyCase; api: AccuracyClient; act: Act; busy: boolean; done: () => void }) {
  const copy = useCopy('accuracy'), questionInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (value) questionInput.current?.focus(); }, [value]);
  const [question, setQuestion] = useState(value?.question ?? ''), [keywords, setKeywords] = useState(value?.expected_keywords.join('\n') ?? '');
  const [source, setSource] = useState(value?.expected_source ?? ''), [decline, setDecline] = useState(value?.expect_no_answer ?? false);
  return <form aria-label={value ? copy.edit : copy.add} className="grid gap-3 rounded-lg border border-border p-4" onSubmit={async e => {
    e.preventDefault(); if (await act(() => api.save({ question, expected_keywords: decline ? [] : keywords.split('\n').map(v => v.trim()).filter(Boolean),
      expected_source: decline ? null : source.trim() || null, expect_no_answer: decline }, value?.id))) {
      setQuestion(''); setKeywords(''); setSource(''); setDecline(false); done();
    }
  }}>
    <h3 className="font-semibold">{value ? copy.edit : copy.add}</h3><p className="text-sm text-muted">{copy.caseHelp}</p>
    <label className="grid gap-1">{copy.question}<textarea ref={questionInput} className={field} required maxLength={4000} value={question} disabled={busy} onChange={e => setQuestion(e.target.value)} /></label>
    <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={decline} disabled={busy} onChange={e => setDecline(e.target.checked)} />{copy.decline}</label>
    {!decline && <><label className="grid gap-1">{copy.keywords}<textarea className={field} maxLength={4019} value={keywords} disabled={busy} onChange={e => setKeywords(e.target.value)} /></label>
      <p className="text-sm text-muted">{copy.keywordHelp}</p><label className="grid gap-1">{copy.source}<input className={field} maxLength={255} value={source} disabled={busy} onChange={e => setSource(e.target.value)} /></label><p className="text-sm text-muted">{copy.sourceHelp}</p></>}
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !decline && !keywords.trim() && !source.trim()}>{copy.saveCase}</Button>
      {value && <Button type="button" variant="secondary" onClick={done}>{copy.cancelEdit}</Button>}</div>
  </form>;
}
export function AccuracyCases({ cases, selected, select, max, admin, api, act, busy }: {
  cases: AccuracyCase[]; selected: string[]; select: (ids: string[]) => void; max: number; admin: boolean;
  api: AccuracyClient; act: Act; busy: boolean;
}) {
  const copy = useCopy('accuracy'), [editing, setEditing] = useState<AccuracyCase>();
  return <section aria-label={copy.cases} className="space-y-3">
    <h2 className="text-xl font-semibold">{copy.cases}</h2><p className="text-sm text-muted">{copy.caseLimit}</p>
    {admin && <details><summary className="min-h-11 cursor-pointer font-medium">{copy.manage}</summary><Editor key={editing?.id ?? 'new'} value={editing} api={api} act={act} busy={busy} done={() => setEditing(undefined)} /></details>}
    {cases.length === 0 && <p>{copy.noCases}</p>}
    {admin && cases.length > 0 && <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => select(cases.slice(0, max).map(c => c.id))}>{copy.selectAll.replace('{max}', String(max))}</Button>
      <Button variant="secondary" disabled={busy} onClick={() => select([])}>{copy.clearSelection}</Button></div>}
    <ul className="grid gap-3 sm:grid-cols-2">{cases.map(c => <li key={c.id} className="min-w-0 space-y-2 break-words rounded-lg border border-border p-4">
      {admin ? <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={selected.includes(c.id)} disabled={busy || !selected.includes(c.id) && selected.length >= max}
        onChange={e => select(e.target.checked ? [...selected, c.id] : selected.filter(id => id !== c.id))} /><span>{c.question}</span></label> : <h3 className="font-medium">{c.question}</h3>}
      <p className="text-sm text-muted">{c.expect_no_answer ? copy.decline : copy.expected + ': ' + [...c.expected_keywords, ...(c.expected_source ? [c.expected_source] : [])].join(' · ')}</p>
      {admin && <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={e => {
        setEditing(c); const section = e.currentTarget.closest('section'); section?.querySelector('details')?.setAttribute('open', '');
      }}>{copy.edit}</Button><Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(copy.removePrompt)) void act(() => api.remove(c.id)); }}>{copy.remove}</Button></div>}
    </li>)}</ul>
  </section>;
}
