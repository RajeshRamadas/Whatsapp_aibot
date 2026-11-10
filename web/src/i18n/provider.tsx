'use client';
import { createContext, useContext, useEffect } from 'react';
import { NextIntlClientProvider } from 'next-intl';
import { catalogFor, type Catalog, type Locale } from './catalogs';
const Context = createContext<Locale>('en');
export function LocaleProvider({ locale, children }: { locale: Locale; children?: React.ReactNode }) {
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  return <Context.Provider value={locale}><NextIntlClientProvider locale={locale} timeZone="Asia/Calcutta"
    messages={catalogFor(locale)}>{children}</NextIntlClientProvider></Context.Provider>;
}
export const useLocale = () => useContext(Context);
export function useCopy<K extends keyof Catalog>(namespace: K): Catalog[K] {
  return catalogFor(useLocale())[namespace];
}
export const useDisplayLocale = () => useLocale() === 'hi' ? 'hi-IN' : 'en-IN';
