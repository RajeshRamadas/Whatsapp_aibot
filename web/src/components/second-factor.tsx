'use client';
import { useEffect, useRef, useState } from 'react';
import { securityClient } from '@/api/security';
import { useCopy } from '@/i18n/provider';
import { Button } from './ui/button';
import { field } from './appointment-settings';
export function SecondFactor({ challenge, complete, restart }: { challenge: string; complete: () => void; restart: () => void }) {
  const copy = useCopy('security'), input = useRef<HTMLInputElement>(null);
  const [code, setCode] = useState(''), [recovery, setRecovery] = useState(false), [error, setError] = useState(false), [busy, setBusy] = useState(false);
  useEffect(() => { input.current?.focus(); }, [recovery]);
  return <div className="space-y-4">
    <h1 className="text-2xl font-semibold">{copy.verifyTitle}</h1><p className="text-sm text-muted">{copy.verifyHelp}</p>
    <form aria-label={copy.verifyTitle} className="grid gap-3" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError(false);
      try { await securityClient.verify(challenge, code.trim()); complete(); }
      catch { setError(true); }
      finally { setBusy(false); setCode(''); }
    }}>
      <label className="grid gap-1">{recovery ? copy.recoveryCode : copy.authenticatorCode}<input ref={input} className={field} required disabled={busy}
        type={recovery ? 'password' : 'text'} inputMode={recovery ? 'text' : 'numeric'} autoComplete={recovery ? 'off' : 'one-time-code'}
        pattern={recovery ? '(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{8}){3})' : '[0-9]{6}'} maxLength={recovery ? 35 : 6}
        value={code} onChange={e => setCode(e.target.value)} /></label>
      {error && <p role="alert">{copy.verifyError}</p>}<Button disabled={busy}>{copy.finishLogin}</Button>
    </form>
    <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => { setRecovery(v => !v); setCode(''); setError(false); }}>{recovery ? copy.useAuthenticator : copy.useRecovery}</Button>
      <Button variant="secondary" disabled={busy} onClick={restart}>{copy.restart}</Button></div>
  </div>;
}
