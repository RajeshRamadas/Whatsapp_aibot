'use client';
import { useEffect, useState } from 'react';
import * as messages from '@/api/bot-messages';
import { useCopy } from '@/i18n/provider';
import { Button } from '@/components/ui/button';
const fields = ['greeting', 'unknown', 'handoff', 'closed', 'unsupported', 'rate', 'error'] as const;
export function BotMessageSettings({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('botMessages');
  const [data, setData] = useState<messages.Catalog>();
  const [language, setLanguage] = useState<messages.Language>('en');
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [loadingError, setLoadingError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revision, reload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    messages.load(workspaceId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setDraft(value.overrides.en || {}); setLoadingError(false); }
    }).catch(() => { if (!controller.signal.aborted) setLoadingError(true); });
    return () => controller.abort();
  }, [workspaceId, revision]);
  function choose(value: messages.Language) {
    setLanguage(value); setDraft({ ...data?.overrides[value] }); setSaved(false); setSaveError(false);
  }
  async function save(reset = false) {
    if (reset && !window.confirm(copy.confirmReset)) return;
    setBusy(true); setSaved(false); setSaveError(false);
    try {
      const value = await messages.save(workspaceId, language, reset ? {} : draft);
      setData(value); setDraft(value.overrides[language] || {}); setSaved(true);
    } catch { setSaveError(true); }
    finally { setBusy(false); }
  }
  return <details className="rounded-xl border border-border p-5 lg:col-span-2">
    <summary className="min-h-11 cursor-pointer py-2 font-semibold">{copy.title}</summary>
    <p className="my-3 text-sm text-muted">{copy.help}</p>
    {!data && !loadingError && <p role="status">{copy.loading}</p>}
    {loadingError && <div role="alert">{copy.loadError} <Button onClick={() => reload(n => n + 1)}>{copy.retry}</Button></div>}
    {data && <form className="space-y-4" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label className="block" htmlFor="bot-message-language">{copy.language}</label>
      <select id="bot-message-language" className="min-h-11 w-full rounded border border-border bg-surface p-2"
        value={language} onChange={event => choose(event.target.value as messages.Language)} disabled={busy}>
        {Object.entries(data.languages).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </select>
      <div className="grid gap-4 sm:grid-cols-2">{fields.map(key => <div key={key}>
        <label className="block" htmlFor={'bot-message-' + key}>{copy[key]}</label>
        <textarea id={'bot-message-' + key} className="min-h-24 w-full rounded border border-border bg-surface p-2"
          value={draft[key] ?? data.defaults[language][key]} maxLength={600} required disabled={busy}
          onChange={event => { setDraft({ ...draft, [key]: event.target.value }); setSaved(false); }} />
      </div>)}</div>
      <div className="flex flex-wrap gap-2"><Button disabled={busy}>{copy.save}</Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => void save(true)}>{copy.reset}</Button></div>
    </form>}
    {saved && <p role="status">{copy.saved}</p>}{saveError && <p role="alert">{copy.saveError}</p>}
  </details>;
}
