'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { QRCodeSVG } from 'qrcode.react';
import { securityClient, type SecuritySetup, type SecurityStatus } from '@/api/security';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { Badge } from './ui/primitives';
import { field } from './appointment-settings';
type Act = (action: () => Promise<void>) => Promise<void>;
function Manage({ action, busy, act, received }: { action: 'disable' | 'regenerate'; busy: boolean; act: Act; received: (codes: string[]) => void }) {
  const copy = useCopy('security'), [password, setPassword] = useState(''), [code, setCode] = useState('');
  const title = action === 'disable' ? copy.disable : copy.regenerate;
  return <details className="rounded-lg border border-border p-4"><summary className="min-h-11 cursor-pointer font-semibold">{title}</summary>
    <form aria-label={title} className="grid gap-3" onSubmit={async e => {
      e.preventDefault(); if (!window.confirm(action === 'disable' ? copy.disablePrompt : copy.regeneratePrompt)) return;
      await act(async () => { try {
        if (action === 'disable') { await securityClient.disable(password, code.trim()); received([]); }
        else received((await securityClient.regenerate(password, code.trim())).recovery_codes);
      } finally { setPassword(''); setCode(''); } });
    }}><p className="text-sm text-muted">{action === 'disable' ? copy.disableHelp : copy.regenerateHelp}</p>
      <label className="grid gap-1">{copy.password}<input className={field} type="password" required autoComplete="current-password" maxLength={4096} disabled={busy} value={password} onChange={e => setPassword(e.target.value)} /></label>
      <label className="grid gap-1">{copy.proof}<input className={field} type="password" required autoComplete="off" maxLength={35} disabled={busy} value={code} onChange={e => setCode(e.target.value)} /></label>
      <Button disabled={busy}>{title}</Button>
    </form>
  </details>;
}
export function AccountSecurity() {
  const copy = useCopy('security');
  const [data, setData] = useState<SecurityStatus>(), [setup, setSetup] = useState<SecuritySetup>(), [codes, setCodes] = useState<string[]>([]);
  const [password, setPassword] = useState(''), [proof, setProof] = useState(''), [busy, setBusy] = useState(false), [revision, refresh] = useState(0);
  const [loadError, setLoadError] = useState(false), [error, setError] = useState(false), [saved, setSaved] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    securityClient.status(controller.signal).then(v => { if (!controller.signal.aborted) {
      setData(v); setLoadError(false); if (v.enabled || !v.setup_pending) setSetup(undefined);
    } }).catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [revision]);
  const act: Act = async action => {
    setBusy(true); setError(false); setSaved(false);
    try { await action(); setSaved(true); }
    catch { setError(true); }
    finally { setBusy(false); refresh(v => v + 1); }
  };
  function downloadCodes() {
    const url = URL.createObjectURL(new Blob([copy.recoveryTitle + '\n\n' + codes.join('\n')], { type: 'text/plain' }));
    const link = document.createElement('a'); link.href = url; link.download = 'recovery-codes.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="mt-5 min-w-0 space-y-5">
    <Button variant="secondary" onClick={() => refresh(v => v + 1)}>{copy.refresh}</Button>
    {!data && !loadError && <p role="status">{copy.loading}</p>}{loadError && <p role="alert">{copy.loadError} <Button onClick={() => refresh(v => v + 1)}>{copy.retry}</Button></p>}
    {error && <p role="alert">{copy.actionError}</p>}{saved && <p role="status">{copy.saved}</p>}
    {data && <><Badge>{data.enabled ? copy.enabled : copy.disabled}</Badge><p className="text-sm text-muted">{copy.accountWide}</p>
      {!data.configured && <p>{copy.unavailable}</p>}
      {codes.length > 0 && <section aria-label={copy.recoveryTitle} className="space-y-3 rounded-xl border border-border p-4">
        <h2 className="text-xl font-semibold">{copy.recoveryTitle}</h2><p>{copy.recoveryHelp}</p>
        <ul className="grid gap-2 sm:grid-cols-2">{codes.map(code => <li key={code} className="break-all font-mono text-sm">{code}</li>)}</ul>
        <div className="flex flex-wrap gap-2"><Button onClick={downloadCodes}>{copy.download}</Button><Button variant="secondary" onClick={() => setCodes([])}>{copy.savedCodes}</Button></div>
      </section>}
      {data.enabled ? <><p>{copy.remaining.replace('{count}', String(data.recovery_remaining))}</p>
        {data.configured && <><Manage action="regenerate" busy={busy} act={act} received={setCodes} /><Manage action="disable" busy={busy} act={act} received={setCodes} /></>}
      </> : !data.email_verified ? <p>{copy.verifyEmail} <Link className="inline-block min-h-11 underline" href="/verify-email">{copy.verifyLink}</Link></p>
        : !data.eligible ? <p>{copy.ineligible}</p> : data.configured && <>
          {!setup ? <form aria-label={copy.begin} className="grid gap-3 rounded-lg border border-border p-4" onSubmit={e => {
            e.preventDefault(); void act(async () => { try { setSetup(await securityClient.setup(password)); } finally { setPassword(''); } });
          }}><h2 className="text-xl font-semibold">{copy.begin}</h2><p className="text-sm text-muted">{copy.beginHelp}</p>
            <label className="grid gap-1">{copy.password}<input className={field} type="password" required autoComplete="current-password" maxLength={4096} disabled={busy} value={password} onChange={e => setPassword(e.target.value)} /></label>
            <Button disabled={busy}>{copy.begin}</Button></form> : <section aria-label={copy.setupTitle} className="space-y-4 rounded-xl border border-border p-4">
            <h2 className="text-xl font-semibold">{copy.setupTitle}</h2><p>{copy.setupHelp}</p>
            <QRCodeSVG value={setup.uri} size={180} role="img" aria-label={copy.qrLabel} className="max-w-full bg-white p-2" />
            <label className="grid gap-1">{copy.manualKey}<input className={field + ' font-mono'} readOnly value={setup.secret} /></label>
            <p className="text-sm text-muted">{copy.setupExpiry}</p>
            <form aria-label={copy.confirm} className="grid gap-3" onSubmit={e => {
              e.preventDefault(); void act(async () => { try { const result = await securityClient.enable(proof.trim()); setCodes(result.recovery_codes); setSetup(undefined); } finally { setProof(''); } });
            }}><label className="grid gap-1">{copy.authenticatorCode}<input className={field} required pattern="[0-9]{6}" maxLength={6} inputMode="numeric" autoComplete="one-time-code" disabled={busy} value={proof} onChange={e => setProof(e.target.value)} /></label>
              <Button disabled={busy}>{copy.confirm}</Button>
            </form><Button variant="secondary" disabled={busy} onClick={() => void act(async () => { await securityClient.cancel(); setSetup(undefined); setProof(''); })}>{copy.cancelSetup}</Button>
          </section>}
        </>}
    </>}
  </div>;
}
