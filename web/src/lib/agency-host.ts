import { cache } from 'react';
import { headers } from 'next/headers';
import type { components } from '@/api/schema';

export const requestAgencyBrand = cache(async (): Promise<components['schemas']['BrandAppearance'] | null> => {
  const host = (await headers()).get('host')?.split(':')[0];
  if (!host?.includes('.')) return null;
  const backend = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
  try {
    const response = await fetch(`${backend}/api/v1/branding?domain=${encodeURIComponent(host)}`, { cache: 'no-store' });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
});
