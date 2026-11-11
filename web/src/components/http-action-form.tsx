'use client';
import { useState } from 'react';
import { actionClient, jsonObject, type CustomAction, type Definition, type ActionAuth, type Parameter } from '@/api/http-actions';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
const emptyAuth: ActionAuth = { kind: 'none', header: 'X-API-Key', value: '', username: '', password: '', token_url: '', client_id: '', client_secret: '', scope: '' };
const empty: Definition = { name: '', description: '', method: 'GET', url_template: '', parameters: [], outputs: { result: '$.result' }, personal_data: true, identity: 'none', allow_changes: false, customer_confirmation: true };
export function HttpActionForm({ row, onSaved, onCancel }: { row?: CustomAction; onSaved: () => void; onCancel: () => void }) {
  const copy = useCopy('actions');
  const [value, setValue] = useState<Definition>(() => row ? { ...empty, ...row.definition, name: row.name } as Definition : empty);
  const [auth, setAuth] = useState<ActionAuth>({ ...emptyAuth, kind: row?.definition.auth_kind as ActionAuth['kind'] ?? 'none' });
  const [headers, setHeaders] = useState('{}'), [body, setBody] = useState(() => JSON.stringify(row?.definition.body_template ?? {}, null, 2));
  const [busy, setBusy] = useState(false), [error, setError] = useState(false);
  function update<K extends keyof Definition>(key: K, next: Definition[K]) { setValue(v => ({ ...v, [key]: next })); }
  function parameter(index: number, change: Partial<Parameter>) { update('parameters', value.parameters?.map((p, i) => i === index ? { ...p, ...change } : p)); }
  const outputs = Object.entries(value.outputs);
  function output(index: number, key: string, path: string) { update('outputs', Object.fromEntries(outputs.map((entry, i) => i === index ? [key, path] : entry))); }
  const authField = (key: keyof ActionAuth, label: string, secret = false, required = true) => <label className="grid gap-1" key={key}>{label}<input className={field} type={secret ? 'password' : key === 'token_url' ? 'url' : 'text'} required={required} autoComplete="off" maxLength={4096} value={String(auth[key] ?? '')} onChange={e => setAuth(v => ({ ...v, [key]: e.target.value }))} /></label>;
  return <form aria-label={row ? copy.edit : copy.add} className="min-w-0 space-y-4 rounded-xl border border-border p-4" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError(false);
    try {
      const { auth_kind: _kind, header_names: _names, ...definition } = value as Definition & { auth_kind?: string; header_names?: string[] };
      void _kind; void _names;
      const parsed = jsonObject(headers);
      if (Object.values(parsed).some(v => typeof v !== 'string')) throw new Error('Header values must be strings');
      await actionClient.save({ ...definition, auth, headers: parsed as Record<string, string>, body_template: value.method === 'GET' ? null : jsonObject(body) }, row?.id);
      setAuth(emptyAuth); setHeaders('{}'); onSaved();
    } catch { setError(true); } finally { setBusy(false); }
  }}>
    <h2 className="text-xl font-semibold">{row ? copy.edit : copy.add}</h2><p className="text-sm text-muted">{copy.formHelp}</p>
    {row && <p role="status">{copy.reenter}</p>}{error && <p role="alert">{copy.saveError}</p>}
    <fieldset disabled={busy} className="min-w-0 space-y-4">
      <div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1">{copy.name}<input className={field} required pattern="[a-z][a-z0-9_]{0,31}" maxLength={32} value={value.name} onChange={e => update('name', e.target.value)} /></label>
        <label className="grid gap-1">{copy.method}<select className={field} value={value.method} onChange={e => update('method', e.target.value as Definition['method'])}>{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m}>{m}</option>)}</select></label></div>
      <label className="grid gap-1">{copy.description}<textarea className={field} required maxLength={500} value={value.description} onChange={e => update('description', e.target.value)} /></label>
      <label className="grid gap-1">{copy.url}<input className={field} required type="url" maxLength={2048} value={value.url_template} onChange={e => update('url_template', e.target.value)} /></label><p className="text-sm text-muted">{copy.urlHelp}</p>
      <fieldset className="min-w-0 space-y-3"><legend className="font-semibold">{copy.parameters}</legend>
        {value.parameters?.map((p, index) => <div key={index} className="grid min-w-0 gap-2 rounded-lg border border-border p-3 sm:grid-cols-2">
          <label className="grid gap-1">{copy.parameterName}<input className={field} required pattern="[a-z][a-z0-9_]{0,31}" maxLength={32} value={p.name} onChange={e => parameter(index, { name: e.target.value })} /></label>
          <label className="grid gap-1">{copy.type}<select className={field} value={p.type} onChange={e => parameter(index, { type: e.target.value as Parameter['type'] })}>{Object.entries(copy.types).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label className="grid gap-1">{copy.parameterDescription}<input className={field} required maxLength={200} value={p.description} onChange={e => parameter(index, { description: e.target.value })} /></label>
          <label className="grid gap-1">{copy.pattern}<input className={field} maxLength={200} value={p.pattern ?? ''} onChange={e => parameter(index, { pattern: e.target.value || null })} /></label>
          <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={p.required} onChange={e => parameter(index, { required: e.target.checked })} />{copy.required}</label>
          <Button type="button" variant="secondary" onClick={() => update('parameters', value.parameters?.filter((_, i) => i !== index))}>{copy.removeParameter}</Button>
        </div>)}<Button type="button" variant="secondary" disabled={(value.parameters?.length ?? 0) >= 12} onClick={() => update('parameters', [...value.parameters ?? [], { name: '', description: '', type: 'string', required: true }])}>{copy.addParameter}</Button>
      </fieldset>
      <fieldset className="min-w-0 space-y-3"><legend className="font-semibold">{copy.outputs}</legend><p className="text-sm text-muted">{copy.outputHelp}</p>
        {outputs.map(([key, path], index) => <div key={index} className="grid min-w-0 gap-2 sm:grid-cols-3">
          <label className="grid gap-1">{copy.outputName}<input className={field} required pattern="[a-z][a-z0-9_]{0,31}" maxLength={32} value={key} onChange={e => output(index, e.target.value, path)} /></label>
          <label className="grid gap-1">{copy.outputPath}<input className={field} required value={path} onChange={e => output(index, key, e.target.value)} /></label>
          <Button className="self-end" type="button" variant="secondary" disabled={outputs.length === 1} onClick={() => update('outputs', Object.fromEntries(outputs.filter((_, i) => i !== index)))}>{copy.removeOutput}</Button>
        </div>)}<Button type="button" variant="secondary" disabled={outputs.length >= 12} onClick={() => update('outputs', { ...value.outputs, ['field_' + (outputs.length + 1)]: '$.field' })}>{copy.addOutput}</Button>
      </fieldset>
      {value.method !== 'GET' && <><label className="grid gap-1">{copy.body}<textarea className={field + ' font-mono'} rows={4} value={body} onChange={e => setBody(e.target.value)} spellCheck={false} /></label>
        <label className="flex min-h-11 items-start gap-3"><input className="mt-1" type="checkbox" required checked={value.allow_changes} onChange={e => update('allow_changes', e.target.checked)} />{copy.allowChanges}</label>
        <label className="flex min-h-11 items-start gap-3"><input className="mt-1" type="checkbox" checked={value.customer_confirmation} onChange={e => update('customer_confirmation', e.target.checked)} />{copy.confirmCustomer}</label></>}
      <fieldset className="min-w-0 space-y-3"><legend className="font-semibold">{copy.privacy}</legend>
        <label className="flex min-h-11 items-start gap-3"><input className="mt-1" type="checkbox" checked={value.personal_data} onChange={e => update('personal_data', e.target.checked)} />{copy.personal}</label>
        <label className="grid gap-1">{copy.identity}<select className={field} value={value.identity} onChange={e => update('identity', e.target.value as Definition['identity'])}>{Object.entries(copy.identities).map(([key, label]) => <option key={key} value={key} disabled={key === 'none' && value.personal_data}>{label}</option>)}</select></label>
        {value.identity !== 'none' && <><label className="grid gap-1">{copy.identityPath}<input className={field} required value={value.identity_path ?? ''} onChange={e => update('identity_path', e.target.value)} /></label><p className="text-sm text-muted">{copy.identityHelp}</p>
          {value.method !== 'GET' && <label className="grid gap-1">{copy.lookup}<input className={field} type="url" required value={value.verification_url ?? ''} onChange={e => update('verification_url', e.target.value)} /></label>}</>}
      </fieldset>
      <fieldset className="min-w-0 space-y-3"><legend className="font-semibold">{copy.authentication}</legend>
        <label className="grid gap-1">{copy.authType}<select className={field} value={auth.kind} onChange={e => setAuth({ ...emptyAuth, kind: e.target.value as ActionAuth['kind'] })}>{Object.entries(copy.authKinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        {auth.kind === 'api_key' && authField('header', copy.header)}
        {(auth.kind === 'api_key' || auth.kind === 'bearer') && authField('value', copy.credential, true)}
        {auth.kind === 'basic' && <>{authField('username', copy.username)}{authField('password', copy.password, true)}</>}
        {auth.kind === 'oauth_client_credentials' && <>{authField('token_url', copy.tokenUrl)}{authField('client_id', copy.clientId)}{authField('client_secret', copy.clientSecret, true)}{authField('scope', copy.scope, false, false)}</>}
        <label className="grid gap-1">{copy.headers}<textarea className={field + ' font-mono'} autoComplete="off" rows={3} value={headers} onChange={e => setHeaders(e.target.value)} spellCheck={false} /></label>
      </fieldset>
      <div className="flex flex-wrap gap-3"><Button disabled={busy}>{copy.save}</Button><Button type="button" variant="secondary" onClick={onCancel}>{copy.cancel}</Button></div>
    </fieldset>
  </form>;
}
