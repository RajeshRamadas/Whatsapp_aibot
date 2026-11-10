'use client';
import { useCopy } from '@/i18n/provider';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';


type Settings = { tenant_id: string; name: string; chat_provider: string; chat_model: string; bot_name: string; bot_persona: string; bot_tone: string; bot_rules: string; business_hours: string; timezone: string; default_language: string; bot_live: boolean };
type Preview = { answer: string; sources: { number: number; filename: string; heading: string | null; page: number | null }[] };
export function BotSettings({ workspaceId }: { workspaceId: string }) {
  const messages = useCopy('base');
  const [settings, setSettings] = useState<Settings | null>(null);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [botName, setBotName] = useState('');
  const [persona, setPersona] = useState('');
  const [tone, setTone] = useState('neutral');
  const [rules, setRules] = useState('');
  const [hours, setHours] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [language, setLanguage] = useState('en');
  const [confirmLive, setConfirmLive] = useState<boolean | null>(null);
  const [question, setQuestion] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState(false);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const headers = { 'X-Workspace-Id': workspaceId };
  useEffect(() => {
    let mounted = true;
    api<Settings>('tenant', { headers: { 'X-Workspace-Id': workspaceId } })
      .then(data => { if (mounted) { setSettings(data); setProvider(data.chat_provider); setModel(data.chat_model); setBotName(data.bot_name); setPersona(data.bot_persona); setTone(data.bot_tone); setRules(data.bot_rules); setHours(data.business_hours); setTimezone(data.timezone); setLanguage(data.default_language); } })
      .catch(() => { if (mounted) setLoadError(true); });
    return () => { mounted = false; };
  }, [workspaceId]);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setLoadError(false); setNotice('');
    try {
      const result = await api<Settings>('tenant', {
        method: 'PATCH', headers, body: JSON.stringify({ chat_provider: provider || null, chat_model: model || null, bot_name: botName, bot_persona: persona, bot_tone: tone, bot_rules: rules, business_hours: hours, timezone, default_language: language }),
      });
      setSettings(result); setProvider(result.chat_provider); setModel(result.chat_model); setNotice(messages.settingsSaved);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
  async function changeLive(live: boolean) {
    setBusy(true); setError(''); setLoadError(false); setNotice('');
    try {
      const result = await api<{ bot_live: boolean }>('bot/live', {
        method: 'POST', headers, body: JSON.stringify({ live, confirm: true }),
      });
      setSettings(current => current ? { ...current, bot_live: result.bot_live } : current);
      setConfirmLive(null);
      setNotice(live ? messages.botIsLive : messages.botIsPaused);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
  async function test(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setLoadError(false); setPreview(null);
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
        <label className="block">{messages.botName}<input required maxLength={100} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={botName} onChange={e => setBotName(e.target.value)} /></label>
        <label className="block">{messages.botPersona}<textarea maxLength={2000} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={persona} onChange={e => setPersona(e.target.value)} /></label>
        <label className="block">{messages.botTone}<select className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={tone} onChange={e => setTone(e.target.value)}><option value="neutral">{messages.toneNeutral}</option><option value="friendly">{messages.toneFriendly}</option><option value="formal">{messages.toneFormal}</option></select></label>
        <label className="block">{messages.botRules}<textarea maxLength={4000} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={rules} onChange={e => setRules(e.target.value)} /></label>
        <label className="block">{messages.businessHours}<textarea maxLength={3000} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3 font-mono text-sm" value={hours} onChange={e => setHours(e.target.value)} placeholder='{"0":["09:00","17:00"]}' /><span className="text-xs text-muted">{messages.businessHoursHelp}</span></label>
        <label className="block">{messages.timezone}<input required maxLength={64} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={timezone} onChange={e => setTimezone(e.target.value)} /></label>
        <label className="block">{messages.defaultLanguage}<input required maxLength={16} className="mt-1 block w-full rounded-lg border border-border bg-surface p-3" value={language} onChange={e => setLanguage(e.target.value)} /></label>
        <details><summary className="cursor-pointer font-medium">{messages.advancedSettings}</summary>
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
        <div className="border-t border-border pt-4"><p role="status" className="mb-3 font-medium">{settings.bot_live ? messages.botIsLive : messages.botIsPaused}</p>
        <Button type="button" variant={settings.bot_live ? 'destructive' : 'primary'} disabled={busy} onClick={() => setConfirmLive(!settings.bot_live)}>{settings.bot_live ? messages.pauseBot : messages.goLive}</Button>
        {confirmLive !== null && <div className="mt-3 rounded-lg border border-border p-3"><p>{confirmLive ? messages.confirmGoLive : messages.confirmPause}</p><div className="mt-3 flex gap-2"><Button type="button" disabled={busy} onClick={() => void changeLive(confirmLive)}>{messages.confirm}</Button><Button type="button" variant="secondary" onClick={() => setConfirmLive(null)}>{messages.cancel}</Button></div></div>}</div>
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
    {(error || notice || loadError) && <p role="status" className="lg:col-span-2">{loadError ? messages.genericError : error || notice}</p>}
  </div>;
}
