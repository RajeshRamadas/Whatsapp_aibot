import { api } from '@/lib/api';
import type { components } from './schema';

export type Tenant = components['schemas']['PlatformTenant'];
export type FailedJob = components['schemas']['FailedJob'];
export type FailedWebhook = components['schemas']['FailedWebhook'];
export const tenants = (offset: number, signal: AbortSignal) =>
  api<components['schemas']['PlatformTenants']>(`platform/tenants?offset=${offset}`, { signal });
export const failedJobs = (offset: number, signal: AbortSignal) =>
  api<components['schemas']['FailedJobs']>(`platform/failed-jobs?offset=${offset}`, { signal });
export const webhookFailures = (offset: number, signal: AbortSignal) =>
  api<components['schemas']['FailedWebhooks']>(`platform/webhook-failures?offset=${offset}`, { signal });
export const suspend = (id: string, suspended: boolean) => api<components['schemas']['Suspension']>(
  `platform/tenants/${encodeURIComponent(id)}/suspension`, { method: 'PUT', body: JSON.stringify({ suspended }) });
export const logout = () => api<void>('auth/logout', { method: 'POST' });
