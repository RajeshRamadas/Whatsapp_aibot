'use client';
import { useEffect, useState } from 'react';
import { tableClient, type SheetCatalog, type SheetTable, type QueryResult } from '@/api/spreadsheets';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
export function SpreadsheetTables({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy('spreadsheets');
  const [data, setData] = useState<SheetCatalog>(), [version, refresh] = useState(0), [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const pending = data?.sources.some(s => ['queued', 'processing', 'uploading'].includes(s.status));
  useEffect(() => {
    const controller = new AbortController(); setLoadError(false);
    tableClient.catalog(controller.signal).then(setData).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [workspaceId, version]);
  useEffect(() => { if (!pending) return; const timer = setInterval(() => refresh(v => v + 1), 3000); return () => clearInterval(timer); }, [pending]);
  async function act(fn: () => Promise<unknown>) { setBusy(true); setError(false); try { await fn(); refresh(v => v + 1); } catch { setError(true); } finally { setBusy(false); } }
  return <section className="mt-6 min-w-0 space-y-5" aria-label={copy.title}>
    <h2 className="text-xl font-semibold">{copy.title}</h2><p>{copy.intro}</p><p className="text-sm text-muted">{copy.policy}</p>
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError}</p>}{error && <p role="alert">{copy.actionError}</p>}
    {data?.sources.length === 0 && <p>{copy.empty}</p>}
    {data?.sources.map(source => <section key={source.id} className="min-w-0 space-y-3 rounded-xl border border-border p-4" aria-label={source.filename}>
      <h3 className="break-all text-lg font-semibold">{source.filename}</h3>
      <p>{source.status === 'ready' ? copy.ready : source.status === 'failed' ? copy.failed : copy.processing}</p>
      {source.error && <p role="alert" className="break-words text-sm">{source.error}</p>}
      {!data.tables.some(t => t.source_id === source.id) && source.status === 'ready' && <p>{copy.needsImport}</p>}
      <Button variant="secondary" disabled={busy || !['ready', 'failed'].includes(source.status)} onClick={() => { if (window.confirm(copy.reimportPrompt)) void act(() => tableClient.reimport(source.id)); }}>{copy.reimport}</Button>
      {data.tables.filter(t => t.source_id === source.id).map(table => <TableCard key={table.id} table={table} ready={source.status === 'ready'} busy={busy} onEnable={() => act(() => tableClient.enable(table.id, !table.enabled))} />)}
    </section>)}
  </section>;
}
function TableCard({ table, ready, busy, onEnable }: { table: SheetTable; ready: boolean; busy: boolean; onEnable: () => Promise<void> }) {
  const copy = useCopy('spreadsheets');
  const [result, setResult] = useState<QueryResult>(), [error, setError] = useState(false), [running, setRunning] = useState(false);
  const [sql, setSql] = useState('SELECT COUNT(*) AS total FROM ' + table.table_name);
  async function read(fn: () => Promise<QueryResult>) { setRunning(true); setError(false); setResult(undefined); try { setResult(await fn()); } catch { setError(true); } finally { setRunning(false); } }
  return <section className="min-w-0 space-y-3 rounded-lg border border-border p-3" aria-label={table.title}>
    <h4 className="break-words font-semibold">{table.title}</h4><p>{copy.rowCount}: {table.row_count} · {table.enabled && ready ? copy.enabled : copy.paused}</p>
    <ul className="flex flex-wrap gap-2 text-sm" aria-label={copy.columns}>{table.columns.map(column => <li className="max-w-full break-words rounded bg-border px-2 py-1" key={column.name}>
      {column.label} <span className="font-mono">({column.name})</span> · {copy.types[column.type as keyof typeof copy.types] ?? column.type}{column.currency && ' · ' + column.currency}</li>)}</ul>
    <div className="flex flex-wrap gap-3"><Button disabled={busy || !ready || running} onClick={() => void onEnable()}>{table.enabled ? copy.pause : copy.enable}</Button>
      <Button variant="secondary" disabled={busy || !ready || running} onClick={() => void read(() => tableClient.preview(table.id))}>{copy.preview}</Button></div>
    <details><summary className="min-h-11 cursor-pointer font-medium">{copy.test}</summary><p className="mt-2 text-sm text-muted">{copy.queryHelp}</p>
      <p className="mt-2 break-all font-mono text-sm">{copy.tableName}: {table.table_name}</p>
      <form className="mt-3 space-y-3" onSubmit={e => { e.preventDefault(); void read(() => tableClient.query(table.id, sql)); }}>
        <label className="grid gap-1">{copy.sql}<textarea className={field + ' font-mono'} rows={4} required maxLength={4000} value={sql} onChange={e => setSql(e.target.value)} spellCheck={false} /></label>
        <Button disabled={busy || !ready || running}>{copy.run}</Button>
      </form>
    </details>
    {running && <p role="status">{copy.running}</p>}{error && <p role="alert">{copy.queryError}</p>}
    {result && <section aria-label={copy.results} className="min-w-0 space-y-3"><h5 className="font-semibold">{copy.results}</h5>
      {result.truncated && <p role="status">{copy.truncated}</p>}{result.rows.length === 0 ? <p>{copy.noMatches}</p> :
      <div className="max-h-96 max-w-full overflow-auto rounded border border-border" role="region" aria-label={copy.resultScroll} tabIndex={0}>
        <table className="w-full border-collapse text-left text-sm"><caption className="sr-only">{copy.results}</caption><thead><tr>{result.columns.map((column, i) => <th scope="col" key={i} className="border-b border-border p-3">{table.columns.find(c => c.name === column)?.label ?? column}</th>)}</tr></thead>
          <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j} className="max-w-xs break-words border-b border-border p-3">{value === null ? copy.blank : String(value)}</td>)}</tr>)}</tbody></table>
      </div>}
      <Button variant="secondary" onClick={() => setResult(undefined)}>{copy.clear}</Button>
    </section>}
  </section>;
}
