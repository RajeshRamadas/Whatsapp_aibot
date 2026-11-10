import { api } from '@/lib/api';
import type { components } from './schema';
export type Catalog = components['schemas']['MessageCatalog'];
export type Language = components['schemas']['MessageOverrides']['language'];
const headers = (id: string) => ({ 'X-Workspace-Id': id });
export const load = (id: string, signal?: AbortSignal) => api<Catalog>('bot/messages', { headers: headers(id), signal });
export const save = (id: string, language: Language, messages: Record<string, string>) => api<Catalog>('bot/messages', {
  method: 'PUT', headers: headers(id), body: JSON.stringify({ language, messages }),
});
