import type { components } from './schema';
import { api } from '@/lib/api';

export type AgencyBilling = components['schemas']['AgencyBilling'];
export const agency = (workspace: string) => {
  const headers = { 'X-Workspace-Id': workspace };
  return {
    billing: (signal: AbortSignal) => api<AgencyBilling>('agency/billing', { headers, signal }),
    link: (client: string) => api(`agency/clients/${encodeURIComponent(client)}`, { headers, method: 'PUT' }),
    unlink: (client: string) => api(`agency/clients/${encodeURIComponent(client)}`, { headers, method: 'DELETE' }),
  };
};
