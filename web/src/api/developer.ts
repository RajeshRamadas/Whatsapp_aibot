import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type ApiKey = S['KeyView'];
export type Scope = NonNullable<S['KeyInput']['scopes']>[number];
export const scopes: Scope[] = ['knowledge:read', 'knowledge:write', 'conversations:read', 'conversations:write', 'messages:send', 'contacts:read', 'contacts:write', 'leads:read', 'leads:write', 'evals:read', 'evals:write'];
export const developerClient = {
  list: (signal?: AbortSignal) => api<ApiKey[]>('api-keys', { signal }),
  create: (name: string, scopes: Scope[], expires_in_days: number) => api<S['CreatedKey']>('api-keys', { method: 'POST', body: JSON.stringify({ name, scopes, expires_in_days }) }),
  revoke: (id: string) => api<void>('api-keys/' + encodeURIComponent(id), { method: 'DELETE' }),
};
