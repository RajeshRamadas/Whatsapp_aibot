'use client';
import { useCopy, useDisplayLocale } from '@/i18n/provider';
import { useEffect, useState } from 'react';
export const control = 'min-h-11 w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 disabled:opacity-50';
export const button = 'min-h-11 rounded-lg border border-border px-4 py-2 disabled:opacity-50';
export function useCampaignText() {
  const copy = useCopy('campaigns');
  const locale = useDisplayLocale();
  return {
    label: (key: string) => (copy.states as Record<string, string>)[key] ?? key,
    reason: (key: string) => (copy.reasons as Record<string, string>)[key] ?? key,
    money: (value: string | number | null, currency: string) => value === null ? copy.unpriced
      : new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 6 }).format(Number(value)),
  };
}
export function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return <label className="grid min-w-0 gap-1 text-sm font-medium">{title}{children}</label>;
}
export function Pager({ page, next, set }: { page: number; next: boolean; set: (page: number) => void }) {
  const copy = useCopy('campaigns');
  return <div className="mt-4 flex flex-wrap items-center gap-3"><button className={button} disabled={!page} onClick={() => set(page - 1)}>{copy.previous}</button>
    <span>{copy.page} {page + 1}</span><button className={button} disabled={!next} onClick={() => set(page + 1)}>{copy.next}</button></div>;
}
export function useRecords<T>(load: (signal: AbortSignal) => Promise<T>, keys: unknown[]) {
  const [data, setData] = useState<T>(); const [error, setError] = useState(false); const [retry, setRetry] = useState(0);
  useEffect(() => { const c = new AbortController(); setError(false);
    load(c.signal).then(value => { if (!c.signal.aborted) setData(value); }).catch(() => { if (!c.signal.aborted) setError(true); });
    return () => c.abort();
    // Callers provide explicit request dependencies; load closures do not define identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...keys, retry]);
  return { data, error, retry: () => setRetry(n => n + 1) };
}
export function Records<T>({ state, children, empty }: { state: ReturnType<typeof useRecords<T>>; children: (data: T) => React.ReactNode; empty?: boolean }) {
  const copy = useCopy('campaigns');
  return state.error ? <div role="alert">{copy.error} <button className={button} onClick={state.retry}>{copy.retry}</button></div>
    : state.data === undefined ? <div role="status"><p>{copy.loading}</p><div aria-hidden="true" className="mt-3 grid gap-3 sm:grid-cols-2">{[0, 1].map(key => <div key={key} className="h-24 rounded-xl border border-border bg-surface motion-safe:animate-pulse" />)}</div></div>
    : empty ? <p>{copy.empty}</p> : <>{children(state.data)}</>;
}
