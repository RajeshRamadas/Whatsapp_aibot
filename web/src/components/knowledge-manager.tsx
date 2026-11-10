'use client';
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { api } from '@/lib/api';
import { failureAdvice, isPending, sha256, statusTone, type Source, type UploadIntent } from '@/lib/knowledge';
import { Badge } from '@/components/ui/primitives';
import { Button } from '@/components/ui/button';
import messages from '@/messages/en.json';

export function KnowledgeManager({ workspaceId, canEdit }: { workspaceId: string; canEdit: boolean }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const headers = { 'X-Workspace-Id': workspaceId };
  const refresh = useCallback(async () => {
    try {
      const data = await api<Source[]>('sources', { headers: { 'X-Workspace-Id': workspaceId } });
      setSources(data);
      setError('');
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setLoading(false); }
  }, [workspaceId]);
  useEffect(() => { void refresh(); }, [refresh]);
  const pending = sources.some(source => isPending(source.status));
  useEffect(() => {
    if (!pending) return;
    const interval = setInterval(() => { void refresh(); }, 3000);
    return () => clearInterval(interval);
  }, [pending, refresh]);
  async function upload(files: FileList | File[]) {
    if (!canEdit || !files.length) return;
    setBusy(true); setError('');
    try {
      for (const file of Array.from(files)) {
        const hash = await sha256(file);
        const intent = await api<UploadIntent>('sources/uploads', {
          method: 'POST', headers, body: JSON.stringify({
            filename: file.name, content_type: file.type || 'application/octet-stream',
            size: file.size, sha256: hash,
          }),
        });
        // Local storage presigned URLs point to the API; route these through our same-origin proxy
        // so session cookies and CSRF remain available. S3 URLs are used as-is.
        const presigned = new URL(intent.upload_url, window.location.origin);
        const local = presigned.pathname.startsWith('/api/v1/sources/uploads/local/');
        const target = local ? '/api/backend/' + presigned.pathname.slice('/api/v1/'.length) + presigned.search : intent.upload_url;
        const putHeaders: Record<string, string> = { 'Content-Type': intent.content_type };
        if (local) {
          putHeaders['X-Workspace-Id'] = workspaceId;
          const csrf = document.cookie.split('; ').find(part => part.startsWith('chatbot_csrf='));
          if (csrf) putHeaders['X-CSRF-Token'] = decodeURIComponent(csrf.slice('chatbot_csrf='.length));
        }
        const result = await fetch(target, { method: 'PUT', headers: putHeaders, body: file });
        if (!result.ok) throw new Error(messages.uploadFailed);
        await api('sources/' + encodeURIComponent(intent.source_id) + '/complete', {
          method: 'POST', headers, body: JSON.stringify({ size: file.size, sha256: hash }),
        });
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); await refresh(); }
  }
  async function remove(id: string) {
    setError('');
    try {
      await api('sources/' + encodeURIComponent(id), { method: 'DELETE', headers });
      setConfirmId(null);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
  }
  function onFiles(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void upload(event.target.files);
    event.target.value = '';
  }
  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault(); setDragging(false);
    if (event.dataTransfer.files) void upload(event.dataTransfer.files);
  }
  return <section aria-label={messages.knowledge} className="mt-6">
    {canEdit && <div onDragOver={event => event.preventDefault()} onDragEnter={event => { event.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)} onDrop={onDrop}
      className={'rounded-xl border-2 border-dashed p-6 text-center ' + (dragging ? 'border-accent' : 'border-border')}>
      <p>{messages.dropFiles}</p><p className="mt-2 text-sm text-muted">{messages.ocrHelp}</p>
      <input ref={input} type="file" multiple accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json,.png,.jpg,.jpeg,.tif,.tiff,.webp"
        className="sr-only" onChange={onFiles} aria-label={messages.selectFiles} />
      <Button type="button" disabled={busy} onClick={() => input.current?.click()}>{busy ? messages.uploading : messages.selectFiles}</Button>
    </div>}
    {error && <div role="alert" className="mt-4 text-red-700 dark:text-red-300">{error} <button type="button" className="underline" onClick={() => void refresh()}>{messages.retry}</button></div>}
    <div className="mt-6 flex items-center justify-between gap-3">
      <h2 className="text-lg font-semibold">{messages.uploadedSources}</h2>
      <Button type="button" variant="secondary" onClick={() => void refresh()}>{messages.refresh}</Button>
    </div>
    {loading && <div role="status" className="mt-4"><p>{messages.loadingSources}</p><div aria-hidden="true" className="mt-2 h-20 rounded-xl border border-border bg-surface motion-safe:animate-pulse" /></div>}
    {!loading && !error && !sources.length && <p className="mt-4 text-muted">{messages.noSources}</p>}
    <ul className="mt-4 grid gap-3">
      {sources.map(source => <li key={source.id} className="rounded-xl border border-border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="w-full break-all font-medium sm:min-w-0 sm:w-auto sm:flex-1">{source.filename}</span>
          <Badge tone={statusTone(source.status)}>{source.status === 'ready' ? messages.ready : source.status === 'failed' ? messages.failed : messages.processing}</Badge>
          {canEdit && <Button type="button" variant="secondary" onClick={() => setConfirmId(source.id)}>{messages.deleteSource}</Button>}
        </div>
        {source.status === 'failed' && <p role="alert" className="mt-2 text-sm">{source.error || messages.genericError}. {messages[failureAdvice(source.error) as keyof typeof messages]}</p>}
        {confirmId === source.id && <div className="mt-3 rounded-lg border border-border p-3">
          <p>{messages.confirmDeleteSource}</p>
          <div className="mt-2 flex gap-2">
            <Button type="button" variant="destructive" onClick={() => void remove(source.id)}>{messages.confirm}</Button>
            <Button type="button" variant="secondary" onClick={() => setConfirmId(null)}>{messages.cancel}</Button>
          </div>
        </div>}
      </li>)}
    </ul>
  </section>;
}
