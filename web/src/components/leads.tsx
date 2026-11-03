'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { Workspace } from '@/components/app-shell';
import { api } from '@/lib/api';
import messages from '@/messages/en.json';
import copy from '@/messages/leads.en.json';

const stages = ['new', 'contacted', 'qualified', 'won', 'lost'] as const;
type Stage = typeof stages[number];
type Lead = {
  id: string; conversation_id: string; fields: Record<string, string>; status: Stage;
  owner_id: string | null; notes: string; created_at: string; source: 'web' | 'whatsapp';
};
type Owner = { id: string; name: string };
const control = 'min-h-11 rounded border border-border bg-surface px-3 py-2';
const size = 30;
const fieldNames = copy.fields as Record<string, string>;

function LeadCard({ lead, owners, editable, save }: {
  lead: Lead; owners: Owner[]; editable: boolean;
  save: (id: string, body: Pick<Lead, 'status' | 'owner_id' | 'notes'>) => Promise<void>;
}) {
  const [status, setStatus] = useState(lead.status);
  const [owner, setOwner] = useState(lead.owner_id ?? '');
  const [notes, setNotes] = useState(lead.notes);
  const [saving, setSaving] = useState(false);
  const phone = (lead.fields.phone ?? '').trim().replace(/[().\s-]/g, '');
  const validPhone = /^\+?[0-9]{7,15}$/.test(phone);
  return <article className="min-w-0 space-y-3 rounded-lg border border-border bg-surface p-4">
    <h3 className="break-words font-semibold">{lead.fields.name || lead.fields.phone || copy.unnamed}</h3>
    <p className="text-sm">{copy.sources[lead.source]} · <time dateTime={lead.created_at}>
      {lead.created_at.slice(0, 10)}
    </time></p>
    <dl className="grid gap-2 break-words text-sm">
      {Object.entries(lead.fields).map(([key, value]) => <div key={key}>
        <dt className="font-medium">{fieldNames[key] ?? key}</dt><dd>{value}</dd>
      </div>)}
    </dl>
    <div className="flex flex-wrap gap-2">
      {validPhone && <a className={control} href={'tel:' + phone}>{copy.call}</a>}
      {validPhone && <a className={control} href={'https://wa.me/' + phone.replace(/^\+/, '')}
        target="_blank" rel="noopener noreferrer">{copy.whatsapp}</a>}
      <Link className={control} href={'/dashboard/inbox?conversation=' + encodeURIComponent(lead.conversation_id)}>
        {copy.conversation}
      </Link>
    </div>
    {editable ? <form className="grid gap-2" onSubmit={async event => {
      event.preventDefault();
      setSaving(true);
      try { await save(lead.id, { status, owner_id: owner || null, notes }); }
      finally { setSaving(false); }
    }}>
      <label className="grid gap-1">{copy.status}
        <select className={control} value={status} onChange={event => setStatus(event.target.value as Stage)}>
          {stages.map(stage => <option key={stage} value={stage}>{copy.stages[stage]}</option>)}
        </select>
      </label>
      <label className="grid gap-1">{copy.owner}
        <select className={control} value={owner} onChange={event => setOwner(event.target.value)}>
          <option value="">{copy.unassigned}</option>
          {owner && !owners.some(item => item.id === owner) &&
            <option value={owner} disabled>{copy.formerOwner}</option>}
          {owners.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="grid gap-1">{copy.notes}
        <textarea className={control} value={notes} maxLength={8000} onChange={event => setNotes(event.target.value)} />
      </label>
      <button className={control} disabled={saving || (status === lead.status && owner === (lead.owner_id ?? '')
        && notes === lead.notes)}>{copy.save}</button>
    </form> : <p className="whitespace-pre-wrap break-words text-sm">
      {copy.stages[lead.status]} · {owners.find(item => item.id === lead.owner_id)?.name ?? copy.unassigned}
      {lead.notes && <><br />{lead.notes}</>}
    </p>}
  </article>;
}

export function Leads({ workspace }: { workspace: Workspace }) {
  const [rows, setRows] = useState<Lead[]>();
  const [owners, setOwners] = useState<Owner[]>([]);
  const [board, setBoard] = useState(false);
  const [filters, setFilters] = useState({ status: '', owner_id: '', source: '', from_date: '', to_date: '' });
  const [offset, setOffset] = useState(0);
  const [revision, refresh] = useState(0);
  const [error, setError] = useState('');
  const editable = workspace.role !== 'viewer';
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  const headers = { 'X-Workspace-Id': workspace.id };

  useEffect(() => {
    const controller = new AbortController();
    api<Owner[]>('lead-owners', { headers: { 'X-Workspace-Id': workspace.id }, signal: controller.signal })
      .then(setOwners).catch(() => { if (!controller.signal.aborted) setError(copy.loadOwnersError); });
    return () => controller.abort();
  }, [workspace.id]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(query);
    params.set('limit', String(size + 1));
    params.set('offset', String(offset));
    api<Lead[]>('leads?' + params, { headers: { 'X-Workspace-Id': workspace.id }, signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setRows(result); })
      .catch(() => { if (!controller.signal.aborted) setError(copy.loadError); });
    return () => controller.abort();
  }, [workspace.id, query, offset, revision]);

  function filter(key: keyof typeof filters, value: string) {
    setFilters(previous => ({ ...previous, [key]: value }));
    setRows(undefined);
    setOffset(0);
    setError('');
  }

  async function save(id: string, body: Pick<Lead, 'status' | 'owner_id' | 'notes'>) {
    setError('');
    try {
      await api('leads/' + encodeURIComponent(id), { method: 'PATCH', headers, body: JSON.stringify(body) });
      refresh(value => value + 1);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.saveError);
    }
  }

  async function download() {
    setError('');
    try {
      const response = await fetch('/api/backend/leads/export?' + query, { headers, cache: 'no-store' });
      if (!response.ok) throw new Error(copy.exportError);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'leads.csv';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError(copy.exportError);
    }
  }

  const cards = (items: Lead[]) => items.map(lead =>
    <LeadCard key={lead.id} lead={lead} owners={owners} editable={editable} save={save} />);
  const visible = rows?.slice(0, size) ?? [];
  return <main className="min-w-0 space-y-4 p-4">
    <h1 className="text-2xl font-semibold">{messages.leads}</h1>
    <div className="flex flex-wrap gap-2">
      <button className={control} aria-pressed={!board} onClick={() => setBoard(false)}>{copy.list}</button>
      <button className={control} aria-pressed={board} onClick={() => setBoard(true)}>{copy.board}</button>
      {editable && <button className={control} onClick={() => void download()}>{copy.export}</button>}
    </div>
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
      <label className="grid gap-1">{copy.status}
        <select className={control} value={filters.status} onChange={event => filter('status', event.target.value)}>
          <option value="">{copy.allStatuses}</option>
          {stages.map(stage => <option key={stage} value={stage}>{copy.stages[stage]}</option>)}
        </select>
      </label>
      <label className="grid gap-1">{copy.owner}
        <select className={control} value={filters.owner_id} onChange={event => filter('owner_id', event.target.value)}>
          <option value="">{copy.allOwners}</option>
          {owners.map(owner => <option key={owner.id} value={owner.id}>{owner.name}</option>)}
        </select>
      </label>
      <label className="grid gap-1">{copy.source}
        <select className={control} aria-label={copy.source} value={filters.source} onChange={event => filter('source', event.target.value)}>
          <option value="">{copy.allSources}</option>
          {Object.entries(copy.sources).map(([source, name]) => <option key={source} value={source}>{name}</option>)}
        </select>
      </label>
      <label className="grid gap-1">{copy.from}
        <input className={control} type="date" value={filters.from_date} max={filters.to_date || undefined}
          onChange={event => filter('from_date', event.target.value)} />
      </label>
      <label className="grid gap-1">{copy.to}
        <input className={control} type="date" value={filters.to_date} min={filters.from_date || undefined}
          onChange={event => filter('to_date', event.target.value)} />
      </label>
    </div>
    {error && <p role="alert">{error}</p>}
    {!rows && !error && <p role="status">{messages.loading}</p>}
    {rows?.length === 0 && <p>{copy.empty}</p>}
    {board ? <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-5">
      {stages.filter(stage => !filters.status || stage === filters.status).map(stage =>
        <section key={stage} aria-label={copy.stages[stage]} className="min-w-0 space-y-2">
          <h2 className="font-semibold">{copy.stages[stage]}</h2>
          {cards(visible.filter(lead => lead.status === stage))}
        </section>)}
    </div> : <div className="grid gap-3 lg:grid-cols-2">{cards(visible)}</div>}
    <div className="flex gap-2">
      {offset > 0 && <button className={control} onClick={() => {
        setRows(undefined); setOffset(value => value - size);
      }}>{copy.previous}</button>}
      {rows && rows.length > size && <button className={control} onClick={() => {
        setRows(undefined); setOffset(value => value + size);
      }}>{copy.next}</button>}
    </div>
  </main>;
}
