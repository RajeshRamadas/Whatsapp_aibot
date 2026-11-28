import type { components } from './schema';
import { api } from '@/lib/api';

export type AgencyBilling = components['schemas']['AgencyBilling'];
export type BrandSettings = components['schemas']['BrandSettings'];
export type BrandView = components['schemas']['BrandView'];
export const agency = (workspace: string) => {
  const headers = { 'X-Workspace-Id': workspace };
  return {
    billing: (signal: AbortSignal) => api<AgencyBilling>('agency/billing', { headers, signal }),
    link: (client: string) => api(`agency/clients/${encodeURIComponent(client)}`, { headers, method: 'PUT' }),
    unlink: (client: string) => api(`agency/clients/${encodeURIComponent(client)}`, { headers, method: 'DELETE' }),
    branding: (signal: AbortSignal) => api<BrandView>('agency/branding', { headers, signal }),
    saveBrand: (body: BrandSettings) => api<BrandView>('agency/branding', { headers, method: 'PUT',
      body: JSON.stringify(body) }),
    verifyDomain: () => api<BrandView>('agency/branding/verify', { headers, method: 'POST' }),
  };
};
