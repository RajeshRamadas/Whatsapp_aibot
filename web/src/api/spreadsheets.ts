import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type SheetTable = S['TableView'];
export type SheetCatalog = S['CatalogView'];
export type QueryResult = S['QueryView'];
export const tableClient = {
  catalog: (signal?: AbortSignal) => api<SheetCatalog>('tables', { signal }),
  preview: (id: string) => api<QueryResult>('tables/' + id + '/preview'),
  enable: (id: string, enabled: boolean) => api<SheetTable>('tables/' + id, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  query: (id: string, sql: string) => api<QueryResult>('tables/' + id + '/query', { method: 'POST', body: JSON.stringify({ sql }) }),
  reimport: (id: string) => api<S['SheetSourceView']>('tables/imports/' + id, { method: 'POST' }),
};
