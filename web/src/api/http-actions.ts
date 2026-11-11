import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type CustomAction = S['ActionView'];
export type Definition = S['Definition'];
export type Parameter = S['Parameter'];
export type ActionAuth = S['Auth'];
export type ActionCall = S['CallView'];
export const actionClient = {
  list: (signal?: AbortSignal) => api<CustomAction[]>('http-actions', { signal }),
  save: (body: Definition, id?: string) => api<CustomAction>('http-actions' + (id ? '/' + id : ''), { method: id ? 'PUT' : 'POST', body: JSON.stringify(body) }),
  enable: (id: string, enabled: boolean) => api<CustomAction>('http-actions/' + id, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  remove: (id: string) => api<void>('http-actions/' + id, { method: 'DELETE' }),
  test: (id: string, body: S['ActionSample']) => api<ActionCall>('http-actions/' + id + '/test', { method: 'POST', body: JSON.stringify(body) }),
  calls: (id: string, signal?: AbortSignal) => api<ActionCall[]>('http-actions/' + id + '/calls', { signal }),
};
export function jsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Object required');
  return parsed as Record<string, unknown>;
}
