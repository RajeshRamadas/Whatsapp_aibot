import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type AccuracyCase = S['CaseView'];
export type AccuracyRun = S['RunSummary'];
export type AccuracyDetail = S['RunDetail'];
export const accuracyClient = (workspace: string) => {
  const request = <T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) => api<T>('evals' + path, {
    method, signal, headers: { 'X-Workspace-Id': workspace }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return {
    load: async (offset: number, signal: AbortSignal) => {
      const [catalog, runs] = await Promise.all([request<S['AccuracyCases']>('/cases', 'GET', undefined, signal),
        request<AccuracyRun[]>('/runs?limit=30&offset=' + offset, 'GET', undefined, signal)]);
      return { ...catalog, runs };
    },
    detail: (id: string, signal: AbortSignal) => request<AccuracyDetail>('/runs/' + id, 'GET', undefined, signal),
    save: (body: S['EvalCaseInput'], id?: string) => id ? request<AccuracyCase>('/' + id, 'PUT', body)
      : request<AccuracyCase[]>('', 'POST', { cases: [body] }),
    remove: (id: string) => request('/' + id, 'DELETE'),
    start: (body: S['RunInput']) => request<AccuracyRun>('/run', 'POST', body),
    cancel: (id: string) => request<AccuracyRun>('/runs/' + id + '/cancel', 'POST', { confirm: true }),
  };
};
export type AccuracyClient = ReturnType<typeof accuracyClient>;
export type Act = (operation: () => Promise<unknown>) => Promise<boolean>;
