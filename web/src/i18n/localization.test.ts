/// <reference types="vite/client" />
import { createElement, useState } from 'react';
import { afterEach, expect, test } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { browserLocale, catalogFor, english, hindi, normalizeLocale, translatedNamespaces } from './catalogs';
import { LocaleProvider, useCopy } from './provider';
afterEach(() => { cleanup(); document.cookie = 'chatbot_locale=; Max-Age=0; Path=/'; document.documentElement.lang = 'en'; });
function Probe() {
  const copy = useCopy('base');
  const [draft, setDraft] = useState('');
  return createElement('div', {}, createElement('button', {}, copy.login),
    createElement('input', { 'aria-label': 'Draft', value: draft, onChange: e => setDraft(e.currentTarget.value) }));
}
function leaves(value: unknown, prefix = ''): Record<string, string> {
  if (typeof value === 'string') return { [prefix]: value };
  return Object.assign({}, ...Object.entries(value as object).map(([key, item]) => leaves(item, prefix + '.' + key)));
}
test('every catalog has Hindi keys and preserves every substitution placeholder', () => {
  const files = import.meta.glob<{ default: unknown }>('../messages/*.en.json', { eager: true });
  expect(Object.keys(english)).toHaveLength(Object.keys(files).length + 1); // base uses en.json
  for (const file of Object.values(files)) expect(Object.values(english)).toContainEqual(file.default);
  for (const name of translatedNamespaces) expect(hindi[name]).not.toBe(english[name]);
  expect(hindi.campaigns.csvSample).toBe(english.campaigns.csvSample);
  expect(hindi.websites.placeholder).toBe(english.websites.placeholder);
  for (const namespace of translatedNamespaces) {
    const en = leaves(english[namespace]), hi = leaves(hindi[namespace]);
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
    for (const key of Object.keys(en)) {
      const parameters = (value: string) => [...value.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map(m => m[1]).sort();
      expect(parameters(hi[key]), namespace + key).toEqual(parameters(en[key]));
    }
  }
});
test('changing interface language preserves unsaved content and keeps catalog contexts isolated', () => {
  const view = render(createElement(LocaleProvider, { locale: 'en' }, createElement(Probe)));
  fireEvent.change(screen.getByLabelText('Draft'), { target: { value: 'SKU-42 ₹1,000 मूल प्रश्न' } });
  view.rerender(createElement(LocaleProvider, { locale: 'hi' }, createElement(Probe)));
  expect(screen.getByRole('button', { name: 'लॉग इन करें' })).toBeTruthy();
  expect((screen.getByLabelText('Draft') as HTMLInputElement).value).toBe('SKU-42 ₹1,000 मूल प्रश्न');
  expect(document.documentElement.lang).toBe('hi');
  const en = render(createElement(Probe));
  expect(within(en.container).getByRole('button', { name: 'Log in' })).toBeTruthy();
  expect(catalogFor('en').base.login).toBe('Log in');
});
test('only supported locale cookie values select a catalog', () => {
  expect(normalizeLocale('../../hi')).toBe('en');
  document.cookie = 'chatbot_locale=hi; Path=/';
  expect(browserLocale()).toBe('hi');
  document.cookie = 'chatbot_locale=%invalid; Path=/';
  expect(browserLocale()).toBe('en');
});
