'use client';
import { useCopy } from '@/i18n/provider';
import { useState } from 'react';
import type { Client, Contact, Segment } from '@/api/campaigns';
import type { components } from '@/api/schema';
import { Badge, Card } from '@/components/ui/primitives';
import { button, control, Field, Pager, Records, useCampaignText, useRecords } from './campaign-ui';

type Catalog = Awaited<ReturnType<Client['catalog']>>;
type Schema = components['schemas'];

export function Templates({ api }: { api: Client }) {
  const copy = useCopy('campaigns');
  const { label } = useCampaignText();
  const [revision, refresh] = useState(0); const state = useRecords(api.catalog, [api, revision]);
  const [draft, setDraft] = useState<Schema['TemplateInput']>({ name: '', language: 'en_US', category: 'MARKETING', body: '', examples: [] });
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const count = new Set(draft.body.match(/\{\{[1-9][0-9]*\}\}/g) ?? []).size;
  async function save(sync: boolean) { setBusy(true); setError(''); try {
    if (sync) await api.write('whatsapp/templates/sync'); else await api.template(draft);
    refresh(n => n + 1);
  } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }
  return <div className="space-y-6"><p>{copy.templateHelp}</p>
    <form aria-label={copy.submitTemplate} className="grid min-w-0 gap-4 rounded-xl border border-border p-5 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void save(false); }}>
      <Field title={copy.name}><input className={control} required pattern="[a-z0-9_]+" maxLength={512} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></Field>
      <Field title={copy.language}><input className={control} required pattern="[a-z]{2,3}(_[A-Z]{2})?" value={draft.language} onChange={e => setDraft({ ...draft, language: e.target.value })} /></Field>
      <Field title={copy.category}><select className={control} value={draft.category} onChange={e => setDraft({ ...draft, category: e.target.value as 'MARKETING' | 'UTILITY' })}><option value="MARKETING">{copy.marketing}</option><option value="UTILITY">{copy.utility}</option></select></Field>
      <Field title={copy.body}><textarea className={control} required maxLength={1024} value={draft.body} onChange={e => {
        const body = e.target.value; const size = new Set(body.match(/\{\{[1-9][0-9]*\}\}/g) ?? []).size;
        setDraft({ ...draft, body, examples: Array.from({ length: size }, (_, i) => draft.examples?.[i] ?? '') }); }} /></Field>
      {Array.from({ length: count }, (_, i) => <Field key={i} title={`${copy.examples} ${i + 1}`}><input className={control} required maxLength={300} value={draft.examples?.[i] ?? ''} onChange={e => setDraft({ ...draft, examples: draft.examples?.map((v, n) => i === n ? e.target.value : v) })} /></Field>)}
      <Card className="whitespace-pre-wrap break-words bg-accent/5"><h2 className="font-semibold">{copy.preview}</h2><p>{draft.body.replace(/\{\{([0-9]+)\}\}/g, (_, i) => draft.examples?.[Number(i) - 1] || copy.sample)}</p></Card>
      <div className="flex flex-wrap gap-2"><button className={button} disabled={busy}>{copy.submitTemplate}</button><button type="button" className={button} disabled={busy} onClick={() => void save(true)}>{copy.sync}</button></div>
      {error && <p role="alert">{error}</p>}
    </form>
    <Records<Catalog> state={state} empty={state.data?.templates.length === 0}>{data => <ul className="grid gap-3 sm:grid-cols-2">{data.templates.map(row => <li key={row.id}><Card className="h-full space-y-2 break-words"><h2 className="font-semibold">{row.name}</h2><Badge tone={row.status === 'APPROVED' ? 'success' : 'warning'}>{label(row.status)}</Badge><p>{row.language} · {row.category}</p><p className="whitespace-pre-wrap">{row.body}</p></Card></li>)}</ul>}</Records>
  </div>;
}

function Consent({ contact, api, changed }: { contact: Contact; api: Client; changed: () => void }) {
  const copy = useCopy('campaigns');

  const [source, setSource] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  return <form className="mt-3 grid gap-2" onSubmit={async e => { e.preventDefault(); if (!window.confirm(copy.consentConfirm + '\n' + (contact.name ?? '') + ' ' + contact.external_user)) return;
    setBusy(true); setError(''); try { await api.consent(contact.id, { opted_in: !contact.marketing_eligible, source }); setSource(''); changed(); }
    catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }}>
    <Field title={copy.source}><input className={control} required maxLength={120} value={source} onChange={e => setSource(e.target.value)} /></Field>
    <button className={button} disabled={busy}>{contact.marketing_eligible ? copy.recordOut : copy.recordIn}</button>{error && <p role="alert">{error}</p>}
  </form>;
}

export function Contacts({ api }: { api: Client }) {
  const copy = useCopy('campaigns');

  const [revision, refresh] = useState(0); const [page, setPage] = useState(0); const [search, setSearch] = useState('');
  const [query, setQuery] = useState(''); const [permission, setPermission] = useState(''); const [file, setFile] = useState<File>();
  const [notice, setNotice] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const state = useRecords(signal => api.get<Schema['ContactPage']>(`contacts?limit=20&offset=${page * 20}&search=${encodeURIComponent(query)}${permission ? '&opted_in=' + permission : ''}`, { signal }), [api, page, query, permission, revision]);
  return <div className="space-y-6"><form className="grid gap-3 rounded-xl border border-border p-5" aria-label={copy.import} onSubmit={async e => { e.preventDefault(); if (!file) return;
    setBusy(true); setError(''); setNotice(''); try { const result = await api.upload(file);
      setNotice(Object.entries(result).reduce((text, [key, value]) => text.replace(`{${key}}`, String(value)), copy.imported)); refresh(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }}>
    <h2 className="text-xl font-semibold">{copy.import}</h2><p>{copy.importHelp}</p><a className="underline" href={'data:text/csv;charset=utf-8,' + encodeURIComponent(copy.csvSample)} download="contacts.csv">{copy.csvExample}</a>
    <Field title={copy.csv}><input className={control} type="file" accept=".csv,text/csv" required onChange={e => setFile(e.target.files?.[0])} /></Field><button className={button} disabled={busy || !file}>{copy.import}</button>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </form><p className="text-sm text-muted">{copy.sourceHelp}</p>
    <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); setPage(0); setQuery(search); }}>
      <Field title={copy.search}><input className={control} value={search} maxLength={200} onChange={e => setSearch(e.target.value)} /></Field>
      <select className={control + ' sm:w-auto'} aria-label={copy.status} value={permission} onChange={e => { setPage(0); setPermission(e.target.value); }}><option value="">{copy.all}</option><option value="true">{copy.optedIn}</option><option value="false">{copy.noConsent}</option></select><button className={button}>{copy.searchButton}</button>
    </form><Records<Schema['ContactPage']> state={state} empty={state.data?.total === 0}>{data => <><ul className="grid gap-3 sm:grid-cols-2">{data.items.map(contact => <li key={contact.id}><Card className="break-words"><h2 className="font-semibold">{contact.name || contact.external_user}</h2><p>{contact.external_user}</p><Badge tone={contact.marketing_eligible ? 'success' : 'warning'}>{contact.marketing_eligible ? copy.optedIn : contact.opt_out_at ? copy.optedOut : copy.noConsent}</Badge><Consent contact={contact} api={api} changed={() => refresh(n => n + 1)} /></Card></li>)}</ul><Pager page={page} set={setPage} next={(page + 1) * 20 < data.total} /></>}</Records>
    <Segments api={api} />
  </div>;
}

function Segments({ api }: { api: Client }) {
  const copy = useCopy('campaigns');
  const [revision, refresh] = useState(0); const [editing, setEditing] = useState<Segment>();
  const [name, setName] = useState(''); const [language, setLanguage] = useState(''); const [eligible, setEligible] = useState(true);
  const [pairs, setPairs] = useState<[string, string][]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const state = useRecords(api.catalog, [api, revision]);
  function edit(row?: Segment) { setEditing(row); setName(row?.name ?? ''); setLanguage(row?.filters.language ?? ''); setEligible(row?.filters.eligible_only ?? true); setPairs(Object.entries(row?.filters.attributes ?? {})); }
  return <section aria-label={copy.segment} className="space-y-4 border-t border-border pt-6"><h2 className="text-xl font-semibold">{copy.contacts}</h2><p>{copy.segmentHelp}</p>
    <form aria-label={copy.saveSegment} className="grid gap-3" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(''); try {
      await api.segment({ name, filters: { channel: 'whatsapp', language: language || null, attributes: Object.fromEntries(pairs), eligible_only: eligible } }, editing?.id); edit(); refresh(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }}>
      <Field title={copy.segmentName}><input className={control} required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
      <Field title={copy.language}><input className={control} value={language} onChange={e => setLanguage(e.target.value)} /></Field>
      {pairs.map(([key, value], index) => <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" key={index}><Field title={copy.field}><input className={control} required pattern="[A-Za-z][A-Za-z0-9_]{0,49}" value={key} onChange={e => setPairs(pairs.map((p, i) => i === index ? [e.target.value, value] : p))} /></Field><Field title={copy.value}><input className={control} required maxLength={200} value={value} onChange={e => setPairs(pairs.map((p, i) => i === index ? [key, e.target.value] : p))} /></Field><button type="button" className={button} onClick={() => setPairs(pairs.filter((_, i) => i !== index))}>{copy.remove}</button></div>)}
      <button type="button" className={button} disabled={pairs.length >= 10} onClick={() => setPairs([...pairs, ['', '']])}>{copy.addFilter}</button>
      <label className="flex items-center gap-2"><input type="checkbox" checked={eligible} onChange={e => setEligible(e.target.checked)} />{copy.eligibleOnly}</label>
      <div className="flex gap-2"><button className={button} disabled={busy}>{copy.saveSegment}</button><button type="button" className={button} onClick={() => edit()}>{copy.newSegment}</button></div>{error && <p role="alert">{error}</p>}
    </form><Records<Catalog> state={state} empty={state.data?.segments.length === 0}>{data => <ul className="space-y-3">{data.segments.map(row => <li key={row.id}><Card className="flex flex-wrap items-center gap-3"><h3 className="mr-auto break-words font-semibold">{row.name}</h3><button className={button} disabled={busy} onClick={() => edit(row)}>{copy.edit}</button><button className={button} disabled={busy} onClick={async () => { if (!window.confirm(copy.deleteConfirm + '\n' + row.name)) return; setBusy(true); try { await api.get(`segments/${row.id}`, { method: 'DELETE' }); refresh(n => n + 1); } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }}>{copy.delete}</button></Card></li>)}</ul>}</Records>
  </section>;
}
