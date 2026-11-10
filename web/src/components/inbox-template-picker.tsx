'use client';
import { useCopy } from '@/i18n/provider';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';


type Template = { id: string; name: string; language: string; body: string };
const field = 'min-h-11 rounded border border-border bg-surface p-2';

export function InboxTemplatePicker({ conversationId, onQueued }: {
  conversationId: string; onQueued: () => void;
}) {
  const copy = useCopy('templates');
  const [templates, setTemplates] = useState<Template[]>();
  const [selected, setSelected] = useState('');
  const [values, setValues] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const template = templates?.find(row => row.id === selected);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const rows: Template[] = [];
      for (let offset = 0; ; offset += 200) {
        const page = await api<Template[]>('whatsapp/templates?approved=true&limit=200&offset=' + offset,
          { signal: controller.signal });
        rows.push(...page);
        if (page.length < 200) break;
      }
      if (!controller.signal.aborted) setTemplates(rows);
    }
    void load().catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => controller.abort();
  }, [copy.loadError]);

  async function send() {
    if (!template || busy || values.some(value => !value.trim())) return;
    setBusy(true);
    setFeedback('');
    setError('');
    const body = JSON.stringify({ template_id: template.id, values: values.map(value => value.trim()) });
    if (attempt.current?.body !== body) attempt.current = { body, key: crypto.randomUUID() };
    try {
      const result = await api<{ status: string }>('inbox/conversations/' + encodeURIComponent(conversationId)
        + '/template', { method: 'POST', body, headers: { 'Idempotency-Key': attempt.current.key } });
      if (result.status === 'failed' || result.status === 'unknown') {
        setError(copy.deliveryUnconfirmed);
      } else {
        setFeedback(copy.queued);
      }
      setSelected('');
      setValues([]);
      onQueued();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.sendError);
    } finally {
      setBusy(false);
    }
  }

  return <form className="mt-4 grid gap-2 rounded border border-border p-3" aria-label={copy.title}
    onSubmit={event => { event.preventDefault(); void send(); }}>
    <h3 className="font-semibold">{copy.title}</h3>
    <p className="text-sm">{copy.windowClosed}</p>
    {!templates && !error && <p role="status">{copy.loading}</p>}
    {templates?.length === 0 && <p>{copy.empty}</p>}
    {!!templates?.length && <label className="grid gap-1">{copy.template}
      <select className={field} value={selected} disabled={busy} onChange={event => {
        const id = event.target.value;
        const row = templates.find(item => item.id === id);
        const count = new Set(row?.body.match(/\{\{[1-9][0-9]*\}\}/g) ?? []).size;
        setSelected(id);
        setValues(Array.from({ length: count }, () => ''));
        setFeedback('');
        setError('');
      }}>
        <option value="">{copy.choose}</option>
        {templates.map(row => <option key={row.id} value={row.id}>{row.name} ({row.language})</option>)}
      </select>
    </label>}
    {values.map((value, index) => <label key={index} className="grid gap-1">
      {copy.variable} {index + 1}
      <input className={field} required maxLength={300} value={value} disabled={busy}
        onChange={event => setValues(old => old.map((item, i) => i === index ? event.target.value : item))} />
    </label>)}
    {template && <>
      <p className="font-medium">{copy.preview}</p>
      <p className="whitespace-pre-wrap break-words">
        {template.body.replace(/\{\{([1-9][0-9]*)\}\}/g, (match, index) => values[Number(index) - 1]?.trim() || match)}
      </p>
      <button className={field} disabled={busy || values.some(value => !value.trim())}>{copy.title}</button>
    </>}
    {error && <p role="alert">{error}</p>}
    {feedback && <p role="status">{feedback}</p>}
  </form>;
}
