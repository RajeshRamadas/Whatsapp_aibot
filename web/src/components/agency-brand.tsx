'use client';

import { createContext, useContext } from 'react';
import type { components } from '@/api/schema';
import { onColor } from '@/lib/agency-colors';

type Brand = components['schemas']['BrandAppearance'];
const Context = createContext<Brand | null>(null);
export function AgencyBrandProvider({ value, children }: { value: Brand | null; children: React.ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export const useAgencyBrand = () => useContext(Context);

export function AgencyBrandMark({ fallback }: { fallback?: string }) {
  const brand = useAgencyBrand();
  if (!brand) return <>{fallback}</>;
  return <span className="inline-flex min-w-0 items-center gap-2 rounded-lg px-2 py-1"
    style={{ backgroundColor: brand.secondary_color, color: onColor(brand.secondary_color) }}>
    {/* A plain image keeps the browser responsible for retrieving the agency's public logo. */}
    {/* eslint-disable-next-line @next/next/no-img-element */}
    {brand.logo_url && <img src={brand.logo_url} alt="" referrerPolicy="no-referrer" className="h-8 w-8 shrink-0 object-contain" />}
    <span className="break-words [overflow-wrap:anywhere]">{brand.display_name}</span>
  </span>;
}
