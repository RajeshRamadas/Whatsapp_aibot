'use client';
import { useCopy } from '@/i18n/provider';
import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { securityClient } from '@/api/security';
import { SecondFactor } from './second-factor';
import { Button } from '@/components/ui/button';
import { Input, Card } from '@/components/ui/primitives';


type Mode = 'signup' | 'login' | 'verify' | 'forgot' | 'reset' | 'invitation' | 'workspace';
type Field = 'email' | 'password' | 'name' | 'code' | 'token';
const fields: Record<Mode, Field[]> = {
  signup: ['name', 'email', 'password'], login: ['email', 'password'],
  verify: ['email', 'code'], forgot: ['email'], reset: ['email', 'code', 'password'],
  invitation: ['token'], workspace: ['name'],
};
const endpoints: Record<Mode, string> = {
  signup: 'auth/signup', login: 'auth/login', verify: 'auth/verify-email',
  forgot: 'auth/password-reset/request', reset: 'auth/password-reset/confirm',
  invitation: 'invitations/accept', workspace: 'workspaces',
};
export function AuthForm({ mode }: { mode: Mode }) {
  const messages = useCopy('base');
  const router = useRouter();
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState('');
  const [challenge, setChallenge] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(''); setBusy(true);
    try {
      const payload: Record<string, string> = {};
      for (const field of fields[mode]) payload[field] = values[field] ?? '';
      const result = mode === 'login' ? await securityClient.login(payload.email, payload.password)
        : await api<{ tenant_id?: string }>(endpoints[mode], { method: 'POST', body: JSON.stringify(payload) });
      if (mode === 'login' && 'mfa_required' in result) {
        setChallenge(result.challenge); setValues(v => ({ ...v, password: '' })); return;
      }
      if (mode === 'workspace' && 'tenant_id' in result && result.tenant_id) {
        document.cookie = 'active_workspace=' + encodeURIComponent(result.tenant_id) + '; Path=/; SameSite=Lax';
      }
      if (mode === 'signup') router.push('/verify-email');
      else if (mode === 'login' || mode === 'invitation' || mode === 'workspace') { router.push('/dashboard'); router.refresh(); }
      else if (mode === 'verify' || mode === 'reset') router.push('/login');
      else setSuccess(messages.requestAccepted);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
  if (challenge) return <Card className="mx-auto w-full max-w-md"><SecondFactor challenge={challenge} complete={() => { router.push('/dashboard'); router.refresh(); }} restart={() => { setChallenge(''); setError(''); }} /></Card>;
  return <Card className="mx-auto w-full max-w-md">
    <h1 className="mb-5 text-2xl font-semibold">{messages[mode]}</h1>
    <form onSubmit={submit} className="grid gap-4">
      {fields[mode].map(field => <label key={field} className="grid gap-1">
        <span>{messages[field]}</span>
        <Input required minLength={field === 'password' ? 10 : undefined}
          type={field === 'password' ? 'password' : field === 'email' ? 'email' : 'text'}
          autoComplete={field === 'password' ? (mode === 'login' ? 'current-password' : 'new-password') : undefined}
          value={values[field] ?? ''} onChange={event => setValues(current => ({ ...current, [field]: event.target.value }))} />
      </label>)}
      {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}
      {success && <p role="status">{success}</p>}
      <Button type="submit" disabled={busy}>{busy ? messages.loading : messages.submit}</Button>
    </form>
    <nav className="mt-5 flex flex-wrap gap-3 text-sm underline">
      <Link href="/login">{messages.login}</Link>
      <Link href="/signup">{messages.signup}</Link>
      <Link href="/forgot-password">{messages.forgot}</Link>
      <Link href="/reset-password">{messages.reset}</Link>
      <Link href="/accept-invitation">{messages.invitation}</Link>
      {mode === 'workspace' && <Link href="/account/security">{messages.accountSecurity}</Link>}
    </nav>
  </Card>;
}
