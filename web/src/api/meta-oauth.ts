import { api } from '@/lib/api';
import type { components } from './schema';
export type MetaAuthorizationView = components['schemas']['MetaOAuthView'];
export type MetaKind = components['schemas']['MetaOAuthStart']['kind'];
const base = 'meta-dm/oauth';
export const metaOAuthClient = {
  config: (signal?: AbortSignal) => api<{ configured: boolean }>(base + '/config', { signal }),
  start: (kind: MetaKind, signal?: AbortSignal) => api<components['schemas']['MetaOAuthURL']>(base + '/start', { method: 'POST', body: JSON.stringify({ kind }), signal }),
  status: (id: string, signal?: AbortSignal) => api<MetaAuthorizationView>(base + '/' + id, { signal }),
  select: (id: string, external_id: string, signal?: AbortSignal) => api<MetaAuthorizationView>(base + '/' + id + '/select', { method: 'POST', body: JSON.stringify({ external_id }), signal }),
  cancel: (id: string, signal?: AbortSignal) => api<MetaAuthorizationView>(base + '/' + id + '/cancel', { method: 'POST', signal }),
};
