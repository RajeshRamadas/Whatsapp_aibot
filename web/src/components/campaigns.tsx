'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { client, type Campaign, type Client, type Contact, type Draft, type Recipient } from '@/api/campaigns';
import type { components } from '@/api/schema';
import { Badge, Card } from '@/components/ui/primitives';
import { Contacts, Templates } from './campaign-resources';
import { button, control, Field, label, money, Pager, reason, Records, useRecords } from './campaign-ui';
import copy from '@/messages/campaigns.en.json';
type Schema = components['schemas'];
type Catalog = Awaited<ReturnType<Client['catalog']>>;

export function Campaigns({ workspaceId }: { workspaceId: string }) {
  const api = useMemo(() => client(workspaceId), [workspaceId]);
  const [tab, setTab] = useState<'campaigns' | 'contacts' | 'templates'>('campaigns');
  return <><nav aria-label={copy.title} className="flex flex-wrap gap-2">{(['campaigns', 'contacts', 'templates'] as const).map(key =>
    <button key={key} className={button + (tab === key ? ' bg-accent text-accent-foreground' : '')} aria-pressed={tab === key} onClick={() => setTab(key)}>{copy[key]}</button>)}</nav>
    {tab === 'contacts' ? <Contacts api={api} /> : tab === 'templates' ? <Templates api={api} /> : <CampaignList api={api} />}</>;
}

function CampaignList({ api }: { api: Client }) {
  const [revision, refresh] = useState(0); const [page, setPage] = useState(0); const [selected, select] = useState<string>();
  const [compose, setCompose] = useState(false); const [online, setOnline] = useState(false);
  const state = useRecords(signal => api.get<Campaign[]>(`campaigns?limit=20&offset=${page * 20}`, { signal }), [api, page, revision]);
  useEffect(() => { document.cookie = 'active_workspace=' + encodeURIComponent(api.workspaceId) + '; Path=/; SameSite=Lax';
    const source = new EventSource('/api/backend/events');
    source.onopen = () => setOnline(true); source.onerror = () => setOnline(false);
    const update = () => refresh(n => n + 1); source.addEventListener('campaign.updated', update);
    const timer = setInterval(update, 5000); return () => { source.close(); clearInterval(timer); };
  }, [api]);
  return <div className="space-y-6"><div className="flex flex-wrap items-center gap-3"><p role="status" className="mr-auto text-sm">{online ? copy.live : copy.reconnecting}</p>
    <button className={button} onClick={() => { setCompose(true); select(undefined); }}>{copy.newCampaign}</button><button className={button} onClick={() => refresh(n => n + 1)}>{copy.refresh}</button></div>
    {compose && <Composer api={api} created={row => { select(row.id); setCompose(false); setPage(0); refresh(n => n + 1); }} />}
    <Records<Campaign[]> state={state} empty={state.data?.length === 0}>{rows => <><ul className="grid gap-3 sm:grid-cols-2">{rows.map(row => <li key={row.id}><Card className="space-y-3 break-words"><h2 className="text-lg font-semibold">{row.name}</h2><Badge>{label(row.state)}</Badge><p>{copy.recipients}: {row.recipients} · {copy.estimate}: {money(row.estimated_max_cost, row.currency)}</p><button className={button} aria-label={`${copy.view}: ${row.name}`} onClick={() => { select(row.id); setCompose(false); }}>{copy.view}</button></Card></li>)}</ul><Pager page={page} set={setPage} next={rows.length === 20} /></>}</Records>
    {selected && <CampaignDetail key={selected} id={selected} api={api} revision={revision} changed={() => refresh(n => n + 1)} />}
  </div>;
}

function Composer({ api, created }: { api: Client; created: (row: Campaign) => void }) {
  const state = useRecords(api.catalog, [api]); const [draft, setDraft] = useState<Draft>({ name: '', template_id: '', segment_id: '', variables: [], currency: 'INR' });
  const [price, setPrice] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const selected = state.data?.templates.find(row => row.id === draft.template_id);
  return <Records<Catalog> state={state}>{catalog => <form aria-label={copy.newCampaign} className="grid gap-4 rounded-xl border border-border bg-surface p-5 sm:grid-cols-2" onSubmit={async e => { e.preventDefault(); setBusy(true); setError('');
    try { created(await api.create({ ...draft, unit_price: price || null })); } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }}>
    <h2 className="text-xl font-semibold sm:col-span-2">{copy.newCampaign}</h2>
    <Field title={copy.campaignName}><input className={control} required maxLength={100} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></Field>
    <Field title={copy.template}><select className={control} required value={draft.template_id} onChange={e => {
      const template = catalog.templates.find(row => row.id === e.target.value);
      const count = new Set(template?.body?.match(/\{\{[1-9][0-9]*\}\}/g) ?? []).size;
      setDraft({ ...draft, template_id: e.target.value, variables: Array.from({ length: count }, () => ({ field: 'name' as const })) }); }}>
      <option value="">{copy.choose}</option>{catalog.templates.map(row => <option key={row.id} value={row.id} disabled={row.status !== 'APPROVED' || !row.body}>{row.name} · {row.language} · {label(row.status)}</option>)}</select></Field>
    <Field title={copy.segment}><select className={control} required value={draft.segment_id} onChange={e => setDraft({ ...draft, segment_id: e.target.value })}><option value="">{copy.choose}</option>{catalog.segments.filter(row => !row.filters.channel || row.filters.channel === 'whatsapp').map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
    <Field title={copy.price}><input className={control} type="number" min="0" step="0.000001" max="999999.999999" value={price} onChange={e => setPrice(e.target.value)} /></Field>
    <Field title={copy.currency}><select className={control} value={draft.currency ?? 'INR'} onChange={e => setDraft({ ...draft, currency: e.target.value as 'INR' | 'USD' })}><option value="INR">INR</option><option value="USD">USD</option></select></Field><p className="text-sm text-muted">{copy.priceHelp}</p>
    {draft.variables?.map((binding, index) => <fieldset key={index} className="space-y-2 rounded-lg border border-border p-3"><legend>{copy.variable} {index + 1}</legend>
      <Field title={copy.binding}><select className={control} value={binding.field} onChange={e => setDraft({ ...draft, variables: draft.variables?.map((v, i) => i === index ? { field: e.target.value as Schema['app__api__campaigns__Binding']['field'] } : v) })}>
        {(['name', 'external_user', 'attribute', 'literal'] as const).map((field, i) => <option key={field} value={field}>{[copy.nameField, copy.phoneField, copy.attributeField, copy.literalField][i]}</option>)}</select></Field>
      {['attribute', 'literal'].includes(binding.field) && <Field title={binding.field === 'attribute' ? copy.field : copy.value}><input className={control} required maxLength={300} value={binding.value ?? ''} onChange={e => setDraft({ ...draft, variables: draft.variables?.map((v, i) => i === index ? { ...v, value: e.target.value } : v) })} /></Field>}
    </fieldset>)}
    {selected && <Card className="whitespace-pre-wrap break-words bg-accent/5"><h3 className="font-semibold">{copy.preview}</h3><p>{selected.body}</p></Card>}
    {(!catalog.segments.length || !catalog.templates.some(row => row.status === 'APPROVED' && row.body)) && <p>{copy.needCatalog}</p>}
    <button className={button} disabled={busy || !draft.template_id || !draft.segment_id}>{copy.create}</button>{error && <p role="alert">{error}</p>}
  </form>}</Records>;
}

function CampaignDetail({ api, id, revision, changed }: { api: Client; id: string; revision: number; changed: () => void }) {
  const [page, setPage] = useState(0); const [outcome, setOutcome] = useState(''); const [phone, setPhone] = useState(''); const [own, setOwn] = useState(false); const [reviewed, setReviewed] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null); const reviewButton = useRef<HTMLButtonElement>(null);
  const scheduleInput = useRef<HTMLInputElement>(null); const [when, setWhen] = useState(''); const [confirmation, setConfirmation] = useState<{ time: string | null }>();
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const state = useRecords(async signal => { const [report, rows] = await Promise.all([
    api.get<Campaign>(`campaigns/${id}`, { signal }), api.get<Recipient[]>(`campaigns/${id}/recipients?limit=30&offset=${page * 30}${outcome ? `&outcome=${outcome}` : ''}`, { signal })]); return { report, rows }; }, [api, id, page, outcome, revision]);
  const loaded = !!state.data;
  useEffect(() => { if (loaded) heading.current?.focus(); }, [id, loaded]);
  const editable = state.data && ['draft', 'paused'].includes(state.data.report.state);
  const testState = state.data?.report.test_state ?? '';
  const failedTest = ['failed', 'unknown', 'suppressed'].includes(testState);
  const pendingTest = ['queued', 'sending'].includes(testState);
  async function act(run: () => Promise<unknown>) { if (busy) return; setBusy(true); setError(''); setNotice(''); try { await run(); changed(); } catch (e) { setError(e instanceof Error ? e.message : copy.error); } finally { setBusy(false); } }
  return <section aria-label={copy.results} className="space-y-4 border-t border-border pt-6"><Records state={state}>{({ report, rows }) => <>
    <div className="flex flex-wrap items-center gap-3"><h2 ref={heading} tabIndex={-1} className="mr-auto text-xl font-semibold">{report.name}</h2><span role="status" aria-label={copy.campaignStatus}><Badge>{label(report.state)}</Badge></span>
      {!['completed', 'cancelled'].includes(report.state) && <button className={button} disabled={busy} onClick={() => { if (window.confirm(copy.cancelConfirm + '\n' + report.name)) void act(() => api.write(`campaigns/${id}/cancel`)); }}>{copy.cancel}</button>}</div>
    {report.reason && <p role="alert">{copy.reason}: {reason(report.reason)}</p>}
    <dl className="grid gap-3 sm:grid-cols-3">{[[copy.recipients, report.recipients], [copy.excluded, report.excluded], [copy.estimate, money(report.estimated_max_cost, report.currency)]].map(([key, value]) => <Card key={key}><dt className="text-sm text-muted">{key}</dt><dd className="text-xl font-semibold">{value}</dd></Card>)}</dl>
    {report.scheduled_at && <p>{copy.scheduled}: <time dateTime={report.scheduled_at}>{new Date(report.scheduled_at).toLocaleString()}</time></p>}
    <div className="grid gap-3 sm:grid-cols-3">{report.preview.map(preview => <Card className="whitespace-pre-wrap break-words bg-accent/5" key={preview.contact_id}><h3 className="font-semibold">{copy.preview}</h3><p className="mt-2">{preview.text}</p></Card>)}</div>
    {editable && <Card className="space-y-4">
      {!report.test_accepted && <form aria-label={copy.test} className="grid gap-3" onSubmit={e => { e.preventDefault(); void act(async () => {
        const number = phone.trim().replace(/^\+/, '');
        const found = await api.get<{ items: Contact[] }>(`contacts?opted_in=true&search=${encodeURIComponent(number)}&limit=200`);
        const contact = found.items.find(row => row.external_user === number); if (!contact) throw new Error(copy.testMissing);
        await api.write(`campaigns/${id}/test`, { contact_id: contact.id, own_number: true } satisfies Schema['app__api__campaigns__TestInput']); setNotice(copy.testQueued);
      }); }}>
        <p>{copy.testHelp}</p><Field title={copy.ownPhone}><input className={control} required type="tel" pattern="\+?[1-9][0-9]{6,14}" value={phone} onChange={e => setPhone(e.target.value)} /></Field>
        <label className="flex items-center gap-2"><input type="checkbox" required checked={own} onChange={e => setOwn(e.target.checked)} />{copy.ownAttestation}</label>
        <button className={button} disabled={busy || !own || failedTest || pendingTest}>{copy.test}</button>{failedTest && <p role="alert">{copy.testFailed}</p>}{pendingTest && <p role="status">{copy.testQueued}</p>}
      </form>}
      {report.test_accepted && <><p>{copy.testAccepted}</p><label className="flex items-center gap-2"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} />{copy.reviewed}</label>
        <Field title={copy.schedule}><input className={control} type="datetime-local" ref={scheduleInput} value={when} onChange={e => setWhen(e.target.value)} /></Field><p className="text-sm">{copy.localTime} {Intl.DateTimeFormat().resolvedOptions().timeZone}</p>
        <button ref={reviewButton} className={button} disabled={busy || !reviewed} onClick={() => { if (!scheduleInput.current?.reportValidity()) return; const date = when ? new Date(when) : null;
          if (date && (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now())) { setError(copy.invalidTime); return; }
          setConfirmation({ time: date?.toISOString() ?? null }); }}>{copy.reviewSend}</button></>}
    </Card>}
    {notice && <p role="status">{notice}</p>}{error && !confirmation && <p role="alert">{error}</p>}
    <h3 className="text-lg font-semibold">{copy.results}</h3><p className="text-sm text-muted">{copy.testsExcluded}</p><dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">{Object.entries(report.results).map(([key, value]) => <Card key={key}><dt className="text-sm text-muted">{label(key)}</dt><dd className="text-xl font-semibold">{value}</dd></Card>)}</dl>
    <p>{copy.billableEstimate}: {money(report.estimated_billable_cost, report.currency)} · {copy.pricingUnknown}: {report.pricing_unconfirmed}</p><p className="text-sm text-muted">{copy.priceHelp} {copy.unknownHelp}</p>
    <Field title={copy.outcome}><select className={control} value={outcome} onChange={e => { setPage(0); setOutcome(e.target.value); }}><option value="">{copy.allOutcomes}</option>{Object.keys(report.results).map(key => <option key={key} value={key}>{label(key)}</option>)}</select></Field>
    <div className="overflow-x-auto rounded-xl border border-border" tabIndex={0} role="region" aria-label={copy.recipientResults}><table className="w-full text-left text-sm"><caption className="sr-only">{copy.recipientResults}</caption><thead><tr>{[copy.contacts, copy.kind, copy.status, copy.attempts, copy.outcome].map(value => <th className="p-3" scope="col" key={value}>{value}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} className="border-t border-border"><td className="p-3">{row.name}<br />{row.phone}</td><td className="p-3">{row.is_test ? copy.testRecipient : copy.audienceRecipient}</td><td className="p-3">{label(row.state)}</td><td className="p-3">{row.attempts}</td><td className="p-3">{[row.error_code && reason(row.error_code), row.replied_at && copy.states.replied, row.opted_out_at && copy.states.opt_outs].filter(Boolean).join(' · ')}</td></tr>)}</tbody></table>{!rows.length && <p className="p-3">{copy.empty}</p>}</div>
    <Pager page={page} next={rows.length === 30} set={setPage} />
    <Dialog.Root open={!!confirmation} onOpenChange={open => { if (!open && !busy) setConfirmation(undefined); }}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" /><Dialog.Content onCloseAutoFocus={event => { event.preventDefault(); (reviewButton.current ?? heading.current)?.focus(); }} className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 space-y-4 overflow-y-auto rounded-xl border border-border bg-surface p-6"><Dialog.Title className="text-xl font-semibold">{copy.confirmTitle}</Dialog.Title><Dialog.Description>{copy.confirmHelp}</Dialog.Description>{error && <p role="alert">{error}</p>}
      <p className="break-words font-semibold">{report.name}</p><p>{copy.recipients}: {report.recipients}</p><p>{copy.estimate}: {money(report.estimated_max_cost, report.currency)}</p><p>{copy.scheduled}: {confirmation?.time ? new Date(confirmation.time).toLocaleString() : copy.now}</p>
      <div className="flex flex-wrap gap-3"><Dialog.Close asChild><button className={button} disabled={busy}>{copy.back}</button></Dialog.Close><button className={button + ' bg-accent text-accent-foreground'} disabled={busy || !reviewed || !report.test_accepted || !editable} onClick={() => void act(async () => { await api.start(id, { confirm: true, scheduled_at: confirmation?.time }); setConfirmation(undefined); })}>{copy.confirmSend}</button></div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </>}</Records></section>;
}
