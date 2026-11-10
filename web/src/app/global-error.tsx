'use client';
import { useEffect, useState } from 'react';
import { captureException } from '@sentry/nextjs';
import './globals.css';
import { browserLocale, catalogFor, type Locale } from '@/i18n/catalogs';

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  const [locale, setLocale] = useState<Locale>('en');
  const copy = catalogFor(locale).errors;
  useEffect(() => { setLocale(browserLocale()); captureException(error); }, [error]);
  return <html lang={locale}><head><title>{copy.title}</title></head><body>
    <main style={{ padding: '2rem', fontFamily: '"Noto Sans Devanagari", sans-serif' }}>
    <h1>{copy.title}</h1><p>{copy.description}</p>
    <button style={{ padding: '.75rem 1rem' }} onClick={reset}>{copy.retry}</button>
  </main></body></html>;
}
