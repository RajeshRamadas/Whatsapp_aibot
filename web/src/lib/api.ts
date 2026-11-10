import { browserLocale, catalogFor } from '@/i18n/catalogs';
export type ApiError = { error?: { code: string; message: string } };
export function csrfToken(cookie: string): string {
  const value = cookie.split('; ').find(part => part.startsWith('chatbot_csrf='));
  return value ? decodeURIComponent(value.slice('chatbot_csrf='.length)) : '';
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())) {
    headers.set('X-CSRF-Token', csrfToken(document.cookie));
  }
  const response = await fetch('/api/backend/' + path.replace(/^\//, ''), {
    ...options, method, headers, credentials: 'same-origin', cache: 'no-store',
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as ApiError;
    throw new Error(data.error?.message ?? catalogFor(browserLocale()).errors.requestFailed);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
