import { api } from '@/lib/api';
import type { components } from './schema';

export type Overview = components['schemas']['AnalyticsOverview'];
export type Filters = { from_date: string; to_date: string; channel: '' | 'web' | 'whatsapp' };
export const overview = (filters: Filters, workspaceId: string, signal: AbortSignal) => {
  const query = new URLSearchParams({ from_date: filters.from_date, to_date: filters.to_date });
  if (filters.channel) query.set('channel', filters.channel);
  return api<Overview>('analytics?' + query, { signal, headers: { 'X-Workspace-Id': workspaceId } });
};
