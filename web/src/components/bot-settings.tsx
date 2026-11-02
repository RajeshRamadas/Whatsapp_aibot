'use client';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import messages from '@/messages/en.json';

type Settings = { tenant_id: string; name: string; chat_provider: string; chat_model: string };
type Preview = { answer: string; sources: { number: number; filename: string; heading: string | null; page: number | null }[] };
export function BotSettings({ workspaceId }: { workspaceId: string }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [question, setQuestion] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const headers = { 'X-Workspace-Id': workspaceId };
  useEffect(() => {
    let mounted = true;
    api<Settings>('tenant', { headers: { 'X-Workspace-Id': workspaceId } })
      .then(data => { if (mounted) { setSettings(data); setProvider(data.chat_provider); setModel(data.chat_model); } })
      .catch(() => { if (mounted) setError(messages.genericError); });
    return () => { mounted = false; };
  }, [workspaceId]);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      const result = await api<Settings>('tenant', {
        method: 'PATCH', headers, body: JSON.stringify({ chat_provider: provider || null, chat_model: model || null }),
      });
      setSettings(result); setProvider(result.chat_provider); setModel(result.chat_model); setNotice(messages.settingsSaved);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
  async function test(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setPreview(null);
    try {
      const result = await api<Preview>('bot/test', { method: 'POST', headers, body: JSON.stringify({ message: question }) });
      setPreview(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
  return <div className="mt-6 grid gap-8 lg:grid-cols-2">
    <section className="rounded-xl border border-border p-5">
      <h2 className="text-lg font-semibold">{messages.botSettings}</h2>
      {!settings && <p role="status">{messages.loading}</p>}
      {settings && <form onSubmit={save} className="mt-5 grid gap-4">
        <details open><summary className="cursor-pointer font-medium">{messages.advancedSettings}</summary>
          <label className="mt-4 block">{messages.provider}
            <select className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={provider} onChange={e => setProvider(e.target.value)}>
              <option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option>
            </select>
          </label>
          <label className="mt-4 block">{messages.model}
            <input className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={model} onChange={e => setModel(e.target.value)} maxLength={100} />
          </label>
        </details>
        <Button type="submit" disabled={busy}>{messages.saveSettings}</Button>
      </form>}
    </section>
    <section className="rounded-xl border border-border p-5">
      <h2 className="text-lg font-semibold">{messages.testChat}</h2>
      <p className="mt-2 text-sm text-muted">{messages.testChatDescription}</p>
      <form onSubmit={test} className="mt-5 grid gap-3">
        <label htmlFor="preview-question">{messages.testQuestion}</label>
        <textarea id="preview-question" required maxLength={4000} value={question} onChange={e => setQuestion(e.target.value)}
          className="min-h-24 w-full rounded-lg border border-border bg-surface p-3" />
        <Button type="submit" disabled={busy || !settings}>{busy ? messages.loading : messages.sendTest}</Button>
      </form>
      {preview && <div aria-live="polite" className="mt-5 rounded-xl border border-border bg-surface p-4">
        <p className="whitespace-pre-wrap">{preview.answer}</p>
        {preview.sources.length > 0 && <details className="mt-3"><summary className="cursor-pointer">{messages.source}</summary>
          <ul className="mt-2 list-inside list-disc text-sm">{preview.sources.map(source =>
            <li key={source.number}>{source.filename}{source.heading ? ' — ' + source.heading : ''}{source.page ? ' (p. ' + source.page + ')' : ''}</li>)}</ul>
        </details>}
      </div>}
    </section>
    {(error || notice) && <p role="status" className="lg:col-span-2">{error || notice}</p>}
  </div>;
}
