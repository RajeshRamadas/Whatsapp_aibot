'use client';
import { useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useCopy, useLocale } from '@/i18n/provider';
import type { Locale } from '@/i18n/catalogs';
export function LanguageSwitch() {
  const copy = useCopy('base');
  const locale = useLocale();
  const router = useRouter();
  const [pending, start] = useTransition();
  function choose(value: Locale) {
    document.cookie = `chatbot_locale=${value}; Path=/; Max-Age=31536000; SameSite=Lax`
      + (location.protocol === 'https:' ? '; Secure' : '');
    start(() => router.refresh());
  }
  return <fieldset className="flex flex-wrap justify-end gap-2 border-b border-border px-3 py-2">
    <legend className="sr-only">{copy.interfaceLanguage}</legend>
    {(['en', 'hi'] as const).map(value => <button key={value} type="button" lang={value}
      className="min-h-11 rounded-lg border border-border px-3 aria-pressed:bg-border"
      aria-pressed={locale === value} disabled={pending} onClick={() => choose(value)}>
      {value === 'en' ? copy.english : copy.hindi}</button>)}
  </fieldset>;
}
