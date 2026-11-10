import { api } from '@/lib/api';
import type { components } from './schema';
export type Website = components['schemas']['WebsiteView'];
export type WebsiteList = components['schemas']['WebsiteList'];
export type WebsiteInput = components['schemas']['WebsiteInput'];
export type Page = components['schemas']['PageView'];
export type Schedule = components['schemas']['Cadence']['schedule'];
const options = (workspace: string) => ({ headers: { 'X-Workspace-Id': workspace } });
const path = (id: string) => `websites/${encodeURIComponent(id)}`;
export const list = (workspace: string, offset: number) =>
  api<WebsiteList>(`websites?offset=${offset}`, options(workspace));
export const create = (workspace: string, body: WebsiteInput) =>
  api<Website>('websites', { ...options(workspace), method: 'POST', body: JSON.stringify(body) });
export const refresh = (workspace: string, id: string) =>
  api<Website>(path(id) + '/refresh', { ...options(workspace), method: 'POST' });
export const schedule = (workspace: string, id: string, schedule: Schedule) =>
  api<Website>(path(id), { ...options(workspace), method: 'PATCH', body: JSON.stringify({ schedule }) });
export const remove = (workspace: string, id: string) =>
  api<void>(path(id), { ...options(workspace), method: 'DELETE' });
export const pages = (workspace: string, id: string) =>
  api<Page[]>(path(id) + '/pages', options(workspace));
