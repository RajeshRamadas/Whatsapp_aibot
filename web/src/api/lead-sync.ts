import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type SyncConfig = S['SyncConfig'];
export type SyncConnection = Omit<S['SyncConnectionView'], 'catalog' | 'config'> & { config: SyncConfig; catalog: {
  fields?: { name: string; label: string; required: boolean }[];
  files?: { id: string; name: string; tabs: { id: number; title: string }[] }[];
} };
export type SyncData = Omit<S['SyncConnections'], 'connections'> & { connections: SyncConnection[] };
export const syncClient = (workspace: string) => {
  const request = <T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) => api<T>(path, {
    method, signal, headers: { 'X-Workspace-Id': workspace }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const path = (id: string) => 'lead-sync/connections/' + id;
  return {
    load: (signal: AbortSignal) => request<SyncData>('lead-sync', 'GET', undefined, signal),
    connect: (body: S['SyncCredentials']) => request('lead-sync/connections', 'POST', body),
    google: (label: string) => request<S['LeadOAuthURL']>('lead-sync/google/connect', 'POST', { label }),
    save: (id: string, body: SyncConfig) => request(path(id), 'PUT', body),
    discover: (id: string, spreadsheet_id?: string) => request(path(id) + '/discover', 'POST', { spreadsheet_id }),
    create: (id: string, title: string) => request(path(id) + '/spreadsheet', 'POST', { title }),
    disconnect: (id: string) => request(path(id), 'DELETE', { confirm: true }),
    jobs: (id: string, signal: AbortSignal) => request<S['SyncJob'][]>(path(id) + '/jobs', 'GET', undefined, signal),
    retry: (id: string, job: string) => request(path(id) + '/jobs/' + job + '/retry', 'POST', { confirm: true }),
    backfill: async (id: string, approve: (count: number) => boolean) => {
      const leads = await request<{ conversation_id: string }[]>('leads?limit=100');
      if (leads.length && approve(leads.length)) return request(path(id) + '/backfill', 'POST', {
        confirm: true, lead_ids: leads.map(v => v.conversation_id),
      });
    },
  };
};
export type SyncClient = ReturnType<typeof syncClient>;
export type Act = (operation: () => Promise<unknown>) => Promise<boolean>;
