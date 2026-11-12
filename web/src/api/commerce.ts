import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type StoreInput = S['StoreInput'];
export type StoreView = S['StoreView'];
export const commerceClient = {
  list: (signal?: AbortSignal) => api<StoreView[]>('commerce', { signal }),
  connect: (body: StoreInput) => api<StoreView>('commerce', { method: 'POST', body: JSON.stringify(body) }),
  test: (id: string) => api<StoreView>('commerce/' + id + '/test', { method: 'POST' }),
  sync: (id: string) => api<StoreView>('commerce/' + id + '/sync', { method: 'POST' }),
  disconnect: (id: string) => api<void>('commerce/' + id, { method: 'DELETE' }),
};
