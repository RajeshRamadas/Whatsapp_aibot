import { api } from '@/lib/api';
import type { components } from './schema';
type Schemas = components['schemas'];
export type Campaign = Schemas['Report'];
export type Contact = Schemas['ContactView'];
export type Segment = Schemas['SegmentView'];
export type Template = Schemas['TemplateView'];
export type Recipient = Schemas['RecipientResult'];
export type Draft = Schemas['CampaignInput'];
export const client = (workspaceId: string) => {
  const get = <T>(path: string, options: RequestInit = {}) => api<T>(path,
    { ...options, headers: { 'X-Workspace-Id': workspaceId } });
  const write = <T>(path: string, body?: unknown, method = 'POST') => get<T>(path,
    { method, body: body === undefined ? undefined : JSON.stringify(body) });
  const all = async <T>(path: string, paged: boolean, signal: AbortSignal) => {
    const result: T[] = [];
    for (let offset = 0; ; offset += 200) {
      const response = await get<T[] | { items: T[]; total: number }>(`${path}?limit=200&offset=${offset}`, { signal });
      const rows = paged ? (response as { items: T[] }).items : response as T[];
      result.push(...rows);
      if (rows.length < 200) return result;
    }
  };
  return { workspaceId, get, write,
    catalog: async (signal: AbortSignal) => {
      const [templates, segments] = await Promise.all([
        all<Template>('whatsapp/templates', false, signal), all<Segment>('segments', true, signal)]);
      return { templates, segments };
    },
    create: (body: Draft) => write<Campaign>('campaigns', body),
    start: (id: string, body: Schemas['StartInput']) => write<Campaign>(`campaigns/${id}/start`, body),
    consent: (id: string, body: Schemas['ConsentInput']) => write<Contact>(`contacts/${id}/consent`, body, 'PUT'),
    segment: (body: Schemas['SegmentInput'], id?: string) => write<Segment>(id ? `segments/${id}` : 'segments', body, id ? 'PUT' : 'POST'),
    template: (body: Schemas['TemplateInput']) => write('whatsapp/templates', body),
    upload: (file: File) => { const body = new FormData(); body.set('file', file);
      return get<Schemas['ImportResult']>('contacts/import', { method: 'POST', body }); },
  };
};
export type Client = ReturnType<typeof client>;
