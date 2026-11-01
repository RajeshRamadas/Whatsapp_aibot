'use client';
import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input, Card } from '@/components/ui/primitives';
import messages from '@/messages/en.json';

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
  const router = useRouter();
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(''); setBusy(true);
    try {
      const payload: Record<string, string> = {};
      for (const field of fields[mode]) payload[field] = values[field] ?? '';
      await api(endpoints[mode], { method: 'POST', body: JSON.stringify(payload) });
      if (mode === 'signup') router.push('/verify-email');
      else if (mode === 'login' || mode === 'invitation' || mode === 'workspace') router.push('/dashboard');
      else if (mode === 'verify' || mode === 'reset') router.push('/login');
      else setSuccess(messages.requestAccepted);
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.genericError); }
    finally { setBusy(false); }
  }
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
    </nav>
  </Card>;
}
