import { api } from '@/lib/api';
import type { components } from './schema';
type Schema = components['schemas'];
export type CalendarData = { accounts: Schema['CalendarAccounts']; bindings: Schema['BindingView'][] };
export const calendarClient = (workspaceId: string) => {
  const request = <T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) => api<T>('google-calendar' + path, {
    method, headers: { 'X-Workspace-Id': workspaceId }, signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return {
    load: async (signal: AbortSignal): Promise<CalendarData> => {
      const [accounts, bindings] = await Promise.all([
        request<Schema['CalendarAccounts']>('', 'GET', undefined, signal),
        request<Schema['BindingView'][]>('/bindings', 'GET', undefined, signal),
      ]);
      return { accounts, bindings };
    },
    connect: (label: string) => request<Schema['ConnectURL']>('/connect', 'POST', { label }),
    refresh: (id: string) => request('/connections/' + id + '/refresh', 'POST'),
    disconnect: (id: string) => request('/connections/' + id, 'DELETE', { confirm: true }),
    bind: (body: Schema['BindingInput']) => request('/bindings', 'POST', body),
    unbind: (id: string) => request('/bindings/' + id, 'DELETE'),
  };
};
