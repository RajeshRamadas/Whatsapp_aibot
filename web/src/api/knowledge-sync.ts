import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type SyncCatalog = S['KnowledgeSyncCatalog'];
export const knowledgeSyncClient = {
  catalog: (signal?: AbortSignal) => api<SyncCatalog>('knowledge-sync', { signal }),
  drive: (body: S['DriveInput']) => api<S['URLView']>('knowledge-sync/drive', { method: 'POST', body: JSON.stringify(body) }),
  notion: (body: S['NotionInput']) => api<S['ConnectionView']>('knowledge-sync/notion', { method: 'POST', body: JSON.stringify(body) }),
  change: (id: string, action: 'pause' | 'resume' | 'sync') => api<S['ConnectionView']>('knowledge-sync/' + id, { method: 'POST', body: JSON.stringify({ action }) }),
  disconnect: (id: string) => api<void>('knowledge-sync/' + id, { method: 'DELETE' }),
};
