import './globals.css';
import type { Metadata } from 'next';
import { english, catalogFor } from '@/i18n/catalogs';
import { requestLocale } from '@/i18n/server';
import { LocaleProvider } from '@/i18n/provider';
import { LanguageSwitch } from '@/components/language-switch';
export const metadata: Metadata = { title: english.base.brand };
export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const locale = await requestLocale();
  return <html lang={locale}><body><LocaleProvider locale={locale}>
    <a href="#main" className="sr-only focus:not-sr-only">{catalogFor(locale).base.back}</a>
    <section aria-label={catalogFor(locale).base.interfaceLanguage}><LanguageSwitch /></section>{children}
  </LocaleProvider></body></html>;
}
