'use client';
import { useCopy } from '@/i18n/provider';
import { useRef, useState } from 'react';
import { mediaUrl, queueMedia, type Attachment } from '@/api/media';

const field = 'min-h-11 min-w-0 rounded border border-border bg-surface p-2';

export function MessageMedia({ conversationId, attachments }: { conversationId: string; attachments: Attachment[] }) {
  const copy = useCopy('media');
  return <ul className="grid gap-3">{attachments.map(item => <li key={item.id} className="min-w-0 space-y-2">
    {item.status === 'ready' && item.mime_type ? <>
      {/* Authenticated URLs refresh their five-minute storage redirect on each request. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {item.kind === 'image' && <img className="max-h-64 max-w-full rounded" src={mediaUrl(conversationId, item)} alt={copy.image} />}
      {item.kind === 'audio' && <audio className="w-full max-w-sm" controls preload="none" aria-label={copy.audio} src={mediaUrl(conversationId, item)} />}
      <a className="block break-words underline" href={mediaUrl(conversationId, item)} target="_blank" rel="noopener noreferrer">{copy.download}: {item.filename}</a>
    </> : item.details ? <details><summary>{copy.details}</summary><pre className="whitespace-pre-wrap break-all text-sm">{JSON.stringify(item.details, null, 2)}</pre></details>
      : <p role="status">{item.status === 'queued' ? copy.pending : copy.unavailable}</p>}
    {item.transcript && <div><h3 className="font-semibold">{copy.transcript}</h3><p className="whitespace-pre-wrap break-words">{item.transcript}</p></div>}
    {item.description && <details><summary>{copy.description}</summary><p className="whitespace-pre-wrap break-words">{item.description}</p></details>}
    {item.interpretation && item.interpretation !== 'ready' && <p className="text-sm">{item.interpretation === 'too_long' ? copy.tooLong : copy.noTranscript}</p>}
  </li>)}</ul>;
}

export function InboxMedia({ conversationId, recipient, onQueued }: { conversationId: string; recipient: string; onQueued: () => void }) {
  const copy = useCopy('media');
  const [file, setFile] = useState<File>(); const [caption, setCaption] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const input = useRef<HTMLInputElement>(null); const attempt = useRef<{ file: File; caption: string; key: string } | undefined>(undefined);
  return <form aria-label={copy.title} className="mt-4 grid min-w-0 gap-2 rounded border border-border p-3" onSubmit={async event => {
    event.preventDefault(); if (!file || busy) return;
    if (file.size > (file.type.startsWith('image/') ? 5242880 : 16777216)) { setError(copy.size); return; }
    if (!window.confirm(copy.confirm + '\n' + file.name + '\n' + recipient)) return;
    if (attempt.current?.file !== file || attempt.current.caption !== caption) attempt.current = { file, caption, key: crypto.randomUUID() };
    setBusy(true); setError(''); setNotice('');
    try { const result = await queueMedia(conversationId, file, caption, attempt.current.key);
      if (['unknown', 'failed'].includes(result.status)) { setError(copy.uncertain); }
      else { setNotice(copy.queued); setFile(undefined); setCaption(''); if (input.current) input.current.value = ''; }
      onQueued();
    } catch (failure) { setError(failure instanceof Error ? failure.message : copy.error); }
    finally { setBusy(false); }
  }}><h3 className="font-semibold">{copy.title}</h3><p className="text-sm">{copy.help}</p>
    <label className="grid min-w-0 gap-1">{copy.file}<input ref={input} className={field + ' w-full'} required type="file" disabled={busy}
      accept="image/jpeg,image/png,application/pdf,text/plain,.doc,.docx,.xls,.xlsx,.ppt,.pptx" onChange={event => setFile(event.target.files?.[0])} /></label>
    <label className="grid gap-1">{copy.caption}<input className={field} disabled={busy} maxLength={1024} value={caption} onChange={event => setCaption(event.target.value)} /></label>
    <button className={field} disabled={busy || !file}>{copy.send}</button>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </form>;
}
