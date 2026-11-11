import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type SecurityStatus = S['MFAStatus'];
export type SecuritySetup = S['MFASetup'];
export type LoginResult = S['UserView'] | S['MFARequired'];
export const securityClient = {
  status: (signal?: AbortSignal) => api<SecurityStatus>('auth/mfa', { signal }),
  setup: (password: string) => api<SecuritySetup>('auth/mfa/setup', { method: 'POST', body: JSON.stringify({ password }) }),
  enable: (code: string) => api<S['MFARecovery']>('auth/mfa/enable', { method: 'POST', body: JSON.stringify({ code }) }),
  cancel: () => api('auth/mfa/setup', { method: 'DELETE' }),
  regenerate: (password: string, code: string) => api<S['MFARecovery']>('auth/mfa/recovery-codes', { method: 'POST', body: JSON.stringify({ password, code }) }),
  disable: (password: string, code: string) => api<S['MFADisabled']>('auth/mfa/disable', { method: 'POST', body: JSON.stringify({ password, code }) }),
  login: (email: string, password: string) => api<LoginResult>('auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  verify: (challenge: string, code: string) => api<S['UserView']>('auth/mfa/verify', { method: 'POST', body: JSON.stringify({ challenge, code }) }),
};
