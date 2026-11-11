'use client';
import type { AccuracyDetail, AccuracyRun } from '@/api/accuracy';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { Button } from './ui/button';
import { Badge } from './ui/primitives';
export function AccuracyHistory({ runs, detail, selected, choose, offset, page, busy, detailError }: {
  runs: AccuracyRun[]; detail?: AccuracyDetail; selected: string; choose: (id: string) => void;
  offset: number; page: (offset: number) => void; busy: boolean; detailError: boolean;
}) {
  const copy = useCopy('accuracy'), locale = useDisplayLocale();
  const percent = (v: number | null) => v === null ? copy.unavailable : new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(v);
  const date = (v: string) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(v));
  const state = (v: string) => (copy.states as Record<string, string>)[v] ?? copy.states.failed;
  const trend = runs.filter(r => r.state === 'complete' && r.context.case_set === detail?.context.case_set).slice().reverse();
  const points = trend.map((r, i) => `${20 + i * 560 / Math.max(1, trend.length - 1)},${110 - (r.accuracy ?? 0) * 100}`);
  return <section aria-label={copy.history} className="min-w-0 space-y-4">
    <h2 className="text-xl font-semibold">{copy.history}</h2><p className="text-sm text-muted">{copy.historyHelp}</p>
    {trend.length > 1 && <figure className="rounded-lg border border-border p-3"><figcaption className="mb-2 text-sm">{copy.trend}</figcaption>
      <svg viewBox="0 0 600 120" className="h-32 w-full" aria-hidden="true"><line x1="20" x2="580" y1="110" y2="110" stroke="currentColor" opacity="0.3" />
        <polyline points={points.join(' ')} fill="none" stroke="currentColor" strokeWidth="2" />{points.map((p, i) => <circle key={trend[i].id} cx={p.split(',')[0]} cy={p.split(',')[1]} r="4" fill="currentColor" />)}</svg>
    </figure>}
    {runs.length === 0 && <p>{copy.noRuns}</p>}
    <ul className="grid gap-3 sm:grid-cols-2">{runs.map(r => <li key={r.id} className="min-w-0 space-y-2 break-words rounded-lg border border-border p-4">
      <h3 className="font-semibold">{r.label}</h3><p className="text-sm">{date(r.created_at)}</p><Badge>{state(r.state)}</Badge>
      <p>{copy.progress.replace('{done}', String(r.completed)).replace('{total}', String(r.total))}</p>
      <p>{copy.passRate}: {percent(r.accuracy)} · {copy.sourceRate}: {percent(r.source_hit_rate)}</p>
      <p className="text-sm text-muted">{String(r.context.provider)} · {String(r.context.model)}</p>
      <Button variant="secondary" disabled={busy} aria-pressed={selected === r.id} onClick={() => choose(r.id)}>{copy.view.replace('{label}', r.label)}</Button>
    </li>)}</ul>
    <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy || offset === 0} onClick={() => page(Math.max(0, offset - 30))}>{copy.newer}</Button>
      <Button variant="secondary" disabled={busy || runs.length < 30} onClick={() => page(offset + 30)}>{copy.older}</Button></div>
    {selected && <section aria-label={copy.results} className="min-w-0 space-y-3 rounded-xl border border-border p-4">
      <h2 className="text-xl font-semibold">{copy.results}</h2>{detailError ? <p role="alert">{copy.loadError}</p> : !detail ? <p role="status">{copy.loading}</p> : <>
        <h3 className="font-semibold">{detail.label}</h3><p>{state(detail.state)} · {copy.progress.replace('{done}', String(detail.completed)).replace('{total}', String(detail.total))}</p>
        <p>{copy.passRate}: {percent(detail.accuracy)} · {copy.sourceRate}: {percent(detail.source_hit_rate)}</p>
        <p className="text-sm text-muted">{copy.fingerprint}: {String(detail.context.case_set).slice(0, 12)} · {copy.knowledge}: {String(detail.context.knowledge).slice(0, 12)}</p>
        <p className="text-sm text-muted">{copy.scoringHelp}</p>
        {detail.errors > 0 && <p role="status">{copy.providerErrors.replace('{count}', String(detail.errors))}</p>}
        {detail.last_error && <p role="status">{(copy.errors as Record<string, string>)[detail.last_error] ?? copy.operationError}</p>}
        <ul className="space-y-3">{detail.cases.map(c => {
          const result = detail.results.find(r => r.id === c.id);
          return <li key={c.id} className="min-w-0 space-y-2 break-words border-t border-border pt-3"><h4 className="font-semibold">{c.question}</h4>
            <p>{copy.expected}: {c.expect_no_answer ? copy.decline : [...c.expected_keywords, ...(c.expected_source ? [c.expected_source] : [])].join(' · ')}</p>
            {!result ? <Badge>{copy.pending}</Badge> : <><Badge>{result.error ? copy.providerError : result.passed ? copy.passed : copy.notPassed}</Badge>
              {result.error ? <p>{copy.operationError}</p> : <><p className="whitespace-pre-wrap">{result.answer}</p><p>{copy.cited}: {(result.sources ?? []).length ? (result.sources ?? []).join(' · ') : copy.none}</p>
                {(result.missing_keywords ?? []).length > 0 && <p>{copy.missing}: {(result.missing_keywords ?? []).join(' · ')}</p>}
                <p className="text-sm text-muted">{Object.entries(result.checks ?? {}).map(([key, passed]) => ((copy.checks as Record<string, string>)[key] ?? copy.expected) + ': ' + (passed ? copy.passed : copy.notPassed)).join(' · ')}</p></>}
            </>}
          </li>;
        })}</ul>
      </>}
    </section>}
  </section>;
}
