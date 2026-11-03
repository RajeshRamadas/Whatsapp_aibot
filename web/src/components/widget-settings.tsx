'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

type Settings = {
  color: string; position: string; icon: string; theme: string; greeting: string;
  show_greeting: boolean; avatar_url: string; name: string; public_key: string;
};
const field = 'min-h-11 rounded border border-border bg-surface p-2';
const options = {
  position: { right: 'Bottom right', left: 'Bottom left' },
  theme: { auto: 'System', light: 'Light', dark: 'Dark' },
  icon: { chat: 'Chat', sparkle: 'Sparkle', help: 'Help' },
};
const labels = { position: 'Launcher position', theme: 'Theme', icon: 'Launcher icon' };

export function WidgetSettings({ workspaceId }: { workspaceId: string }) {
  const [draft, setDraft] = useState<Settings>();
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<Settings>('widget-settings', { headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setDraft(value); })
      .catch(() => { if (!controller.signal.aborted) setError('Could not load widget settings.'); });
    return () => controller.abort();
  }, [workspaceId]);

  function change<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft(previous => previous ? { ...previous, [key]: value } : previous);
    setSaved(false);
  }
  async function save() {
    if (!draft || busy) return;
    setBusy(true);
    setError('');
    try {
      const { color, position, icon, theme, greeting, show_greeting, avatar_url } = draft;
      await api('widget-settings', {
        method: 'PUT', headers: { 'X-Workspace-Id': workspaceId },
        body: JSON.stringify({ color, position, icon, theme, greeting, show_greeting, avatar_url }),
      });
      setSaved(true);
    } catch { setError('Could not save widget settings.'); }
    finally { setBusy(false); }
  }

  return <details className="mt-6 rounded border border-border p-4">
    <summary className="cursor-pointer font-semibold">Website widget</summary>
    {error && <p role="alert">{error}</p>}
    {!draft && !error && <p role="status">Loading widget settings…</p>}
    {draft && <div className="mt-4 grid gap-4 md:grid-cols-2">
      <form className="grid gap-3" onSubmit={event => { event.preventDefault(); void save(); }}>
        <label className="grid gap-1">Accent colour
          <input className={field} type="color" value={draft.color} onChange={e => change('color', e.target.value)} />
        </label>
        {(['position', 'theme', 'icon'] as const).map(key => <label key={key} className="grid gap-1">
          {labels[key]}
          <select className={field} value={draft[key]} onChange={e => change(key, e.target.value)}>
            {Object.entries(options[key]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>)}
        <label className="grid gap-1">Avatar HTTPS URL
          <input className={field} type="url" pattern="https://.+" value={draft.avatar_url} maxLength={1024}
            onChange={e => change('avatar_url', e.target.value)} />
        </label>
        <label className="grid gap-1">Greeting
          <input className={field} value={draft.greeting} maxLength={160}
            onChange={e => change('greeting', e.target.value)} />
        </label>
        <label className="flex min-h-11 items-center gap-2">
          <input type="checkbox" checked={draft.show_greeting} onChange={e => change('show_greeting', e.target.checked)} />
          Show the greeting once per visitor
        </label>
        <button className={field} disabled={busy}>Save widget settings</button>
        {saved && <p role="status">Widget settings saved.</p>}
      </form>
      <div className="min-w-0 space-y-3">
        <h3 className="font-semibold">Live preview</h3>
        <div className={'relative h-56 overflow-hidden rounded border border-border p-3 ' +
          (draft.theme === 'dark' ? 'bg-slate-900 text-white' : draft.theme === 'auto'
            ? 'bg-white text-black dark:bg-slate-900 dark:text-white' : 'bg-white text-black')}>
          <div className="flex items-center gap-2 border-b-4 pb-2" style={{ borderColor: draft.color }}>
            {/^https:\/\//i.test(draft.avatar_url) && <span aria-hidden="true" className="h-8 w-8 rounded-full bg-cover"
              style={{ backgroundImage: 'url(' + JSON.stringify(draft.avatar_url) + ')' }} />}
            <strong className="break-all">{draft.name}</strong>
          </div>
          <div className={'absolute bottom-3 max-w-[85%] space-y-2 ' + (draft.position === 'left' ? 'left-3' : 'right-3')}>
            {draft.show_greeting && <p className="break-words rounded border border-border p-2">{draft.greeting}</p>}
            <span className="inline-block rounded-full border-4 px-3 py-1" style={{ borderColor: draft.color }}>
              {draft.icon === 'chat' ? 'Chat' : draft.icon === 'sparkle' ? '✦' : '?'}
            </span>
          </div>
        </div>
        <p className="text-sm">Replace YOUR-API with your public API host, then add this script to an allowed website.</p>
        <pre className="whitespace-pre-wrap break-all rounded border border-border p-2 text-xs">
          {'<script async src="https://YOUR-API/widget.js" data-chatbot-key="' + draft.public_key + '"></script>'}
        </pre>
      </div>
    </div>}
  </details>;
}
