import './globals.css';
import type { Metadata } from 'next';
import { english, catalogFor } from '@/i18n/catalogs';
import { requestLocale } from '@/i18n/server';
import { LocaleProvider } from '@/i18n/provider';
import { LanguageSwitch } from '@/components/language-switch';
import { AgencyBrandProvider } from '@/components/agency-brand';
import { requestAgencyBrand } from '@/lib/agency-host';
import { onColor } from '@/lib/agency-colors';
export async function generateMetadata(): Promise<Metadata> {
  const brand = await requestAgencyBrand();
  return { title: brand?.display_name ?? english.base.brand };
}
export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const [locale, brand] = await Promise.all([requestLocale(), requestAgencyBrand()]);
  const theme = brand ? { '--accent': brand.primary_color,
    '--accent-foreground': onColor(brand.primary_color), '--focus-color': 'var(--foreground)' } : undefined;
  return <html lang={locale} style={theme as React.CSSProperties}>
    <body><LocaleProvider locale={locale}><AgencyBrandProvider value={brand}>
    <a href="#main" className="sr-only focus:not-sr-only">{catalogFor(locale).base.skipContent}</a>
    <section aria-label={catalogFor(locale).base.interfaceLanguage}><LanguageSwitch /></section>{children}
  </AgencyBrandProvider></LocaleProvider></body></html>;
}
