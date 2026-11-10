import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, csrfToken } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('csrfToken', () => {
  it('reads double-submit token', () => expect(csrfToken('x=1; chatbot_csrf=a%2Bb; y=2')).toBe('a+b'));
  it('returns empty when missing', () => expect(csrfToken('x=1')).toBe(''));
});
it('lets the browser supply multipart boundaries while retaining CSRF protection', async () => {
  document.cookie = 'chatbot_csrf=test-csrf';
  const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 })); vi.stubGlobal('fetch', fetch);
  const body = new FormData(); body.set('file', new File(['phone\n15550000001'], 'contacts.csv'));
  await api('contacts/import', { method: 'POST', body });
  const options = fetch.mock.calls[0][1];
  expect(options.body).toBe(body); expect(options.headers.has('Content-Type')).toBe(false);
  expect(options.headers.get('X-CSRF-Token')).toBe('test-csrf');
});
