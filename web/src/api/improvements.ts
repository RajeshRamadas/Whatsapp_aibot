import { api } from '@/lib/api';
import type { components, paths } from './schema';
type Query = NonNullable<paths['/api/v1/improvements']['get']['parameters']['query']>;
export type Review = components['schemas']['ReviewView'];
export type ReviewList = components['schemas']['ReviewList'];
export type Correction = components['schemas']['Correction'];
export type Filters = { from_date: string; to_date: string; channel: '' | 'web' | 'whatsapp';
  state: NonNullable<Query['state']>; reason: NonNullable<Query['reason']> };
const options = (workspace: string) => ({ headers: { 'X-Workspace-Id': workspace } });
export const list = (workspace: string, filters: Filters, offset: number, signal?: AbortSignal) => {
  const query = new URLSearchParams({ ...filters, offset: String(offset) });
  if (!filters.channel) query.delete('channel');
  return api<ReviewList>('improvements?' + query, { ...options(workspace), signal });
};
export const correct = (workspace: string, id: string, body: Correction) =>
  api<Review>(`improvements/${encodeURIComponent(id)}/answer`, {
    ...options(workspace), method: 'POST', body: JSON.stringify(body),
  });
export const resolve = (workspace: string, id: string, state: 'open' | 'dismissed') =>
  api<Review>(`improvements/${encodeURIComponent(id)}`, {
    ...options(workspace), method: 'PATCH', body: JSON.stringify({ state }),
  });
