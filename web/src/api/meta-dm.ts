import { api } from '@/lib/api';
import type { components } from './schema';
export type MetaConnect = components['schemas']['Connect'];
export type MetaChannel = { id: string; kind: string; external_id: string; display_name: string | null; status: string; meta_bot_enabled?: boolean; meta_token_status?: 'valid' | 'expiring' | 'invalid' | null };
export const metaClient = {
  list: (signal?: AbortSignal) => api<MetaChannel[]>('channels', { signal }),
  connect: (body: MetaConnect) => api<MetaChannel>('meta-dm/channels', { method: 'POST', body: JSON.stringify(body) }),
  setBot: (id: string, enabled: boolean) => api<components['schemas']['MetaBotSetting']>('meta-dm/channels/' + id + '/bot', { method: 'PUT', body: JSON.stringify({ enabled } satisfies components['schemas']['MetaBotChange']) }),
  disconnect: (id: string) => api<void>('meta-dm/channels/' + id, { method: 'DELETE' }),
};
