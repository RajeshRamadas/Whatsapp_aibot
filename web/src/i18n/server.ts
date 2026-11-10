import { cookies } from 'next/headers';
import { catalogFor, normalizeLocale, type Catalog } from './catalogs';
export async function requestLocale() { return normalizeLocale((await cookies()).get('chatbot_locale')?.value); }
export async function getCopy<K extends keyof Catalog>(namespace: K): Promise<Catalog[K]> {
  return catalogFor(await requestLocale())[namespace];
}
