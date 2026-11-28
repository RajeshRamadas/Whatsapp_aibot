'use client';

import { useEffect, useState } from 'react';
import type { Template } from '@/api/campaigns';
import { leadFollowUps, type FollowUpView } from '@/api/lead-follow-ups';
import { useCopy } from '@/i18n/provider';

const field = 'min-h-11 min-w-0 rounded border border-border bg-surface p-2';

export function LeadFollowUps({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('leads').followUp;
  const base = useCopy('base');
  const [saved, setSaved] = useState<FollowUpView>();
  const [templates, setTemplates] = useState<Template[]>();
  const [templateId, setTemplateId] = useState('');
  const [values, setValues] = useState<string[]>([]);
  const [delay, setDelay] = useState('24');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<'load' | 'save' | null>(null);
  const [templateError, setTemplateError] = useState(false);
  const [feedback, setFeedback] = useState(false);
  const selected = templates?.find(row => row.id === templateId);
  const parameterCount = new Set(selected?.body?.match(/\{\{[1-9][0-9]*\}\}/g) ?? []).size;
  const parameters = Array.from({ length: parameterCount }, (_, index) => values[index] ?? '');

  function apply(rule: FollowUpView) {
    setSaved(rule);
    setTemplateId(rule.template_id ?? '');
    setValues(rule.values ?? []);
    setDelay(String(rule.delay_hours ?? 24));
  }

  useEffect(() => {
    const controller = new AbortController();
    const client = leadFollowUps(workspaceId);
    client.read(controller.signal).then(rule => { if (!controller.signal.aborted) apply(rule); })
      .catch(() => { if (!controller.signal.aborted) setError('load'); });
    client.templates(controller.signal).then(rows => { if (!controller.signal.aborted) setTemplates(rows); })
      .catch(() => { if (!controller.signal.aborted) setTemplateError(true); });
    return () => controller.abort();
  }, [workspaceId]);

  async function save(enabled: boolean) {
    if (!saved || busy) return;
    setBusy(true); setError(null); setFeedback(false);
    const body = enabled ? { enabled, template_id: templateId, values: parameters, delay_hours: Number(delay) }
      : { enabled };
    try {
      apply(await leadFollowUps(workspaceId).save(body));
      setFeedback(true);
    } catch {
      setError('save');
    } finally {
      setBusy(false);
    }
  }

  return <section aria-labelledby="lead-follow-up-heading" className="min-w-0 space-y-3 rounded-xl border border-border p-4">
    <h2 id="lead-follow-up-heading" className="text-lg font-semibold">{copy.title}</h2>
    <p className="text-sm">{copy.description}</p>
    {error && <p role="alert">{error === 'load' ? copy.loadError : copy.saveError}</p>}
    {!saved && !error && <p role="status">{base.loading}</p>}
    {saved && <form className="grid gap-3" onSubmit={event => { event.preventDefault(); void save(true); }}>
      <p className="text-sm font-medium">{saved.enabled ? copy.enabled : copy.paused}</p>
      {saved.enabled && !saved.template_available && <p role="alert">{copy.templateChanged}</p>}
      {templateError && <p role="alert">{copy.templateError}</p>}
      {!templates && !templateError && <p role="status">{copy.loadingTemplates}</p>}
      {templates?.length === 0 && <p>{copy.noTemplates}</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid min-w-0 gap-1">{copy.template}
          <select required className={field} value={templateId} disabled={busy || !templates?.length}
            onChange={event => {
              setTemplateId(event.target.value);
              setValues([]);
            }}>
            <option value="">{copy.chooseTemplate}</option>
            {templateId && !selected && <option value={templateId} disabled>{copy.unavailableTemplate}</option>}
            {templates?.map(row => <option value={row.id} key={row.id}>{row.name} ({row.language})</option>)}
          </select>
        </label>
        <label className="grid min-w-0 gap-1">{copy.delay}
          <input required type="number" min="1" max="168" className={field} value={delay} disabled={busy}
            onChange={event => setDelay(event.target.value)} />
        </label>
      </div>
      {selected?.body && <p className="whitespace-pre-wrap break-words rounded bg-surface p-2 text-sm">
        {selected.body.replace(/\{\{([1-9][0-9]*)\}\}/g, (part, number) => parameters[Number(number) - 1] || part)}
      </p>}
      {parameters.map((value, index) => <label className="grid gap-1" key={index}>
        {copy.variable.replace('{number}', String(index + 1))}
        <input required maxLength={300} className={field} value={value} disabled={busy}
          onChange={event => setValues(parameters.map((item, i) => i === index ? event.target.value : item))} />
      </label>)}
      <div className="flex flex-wrap gap-2">
        <button className={field} disabled={busy || !selected || parameters.some(value => !value.trim())}>
          {saved.enabled ? copy.save : copy.enable}
        </button>
        {saved.enabled && <button type="button" className={field} disabled={busy} onClick={() => void save(false)}>
          {copy.pause}
        </button>}
      </div>
    </form>}
    {feedback && <p role="status">{copy.saved}</p>}
  </section>;
}
