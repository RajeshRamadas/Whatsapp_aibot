import { test, expect } from '@playwright/test';

test('website orders require a conversation-bound one-time code and mask saved transcripts', async ({ request }) => {
  test.setTimeout(90000);
  expect((await request.post('/api/backend/auth/login', { data: { email: 'order-proof@example.com', password: 'commerce-browser-passphrase' } })).ok()).toBeTruthy();
  const state = await request.storageState();
  const workspaces = await (await request.get('/api/backend/workspaces')).json();
  const auth = { headers: { 'X-CSRF-Token': decodeURIComponent(state.cookies.find(c => c.name === 'chatbot_csrf')!.value), 'X-Workspace-Id': workspaces[0].tenant_id } };
  const connected = await request.post('/api/backend/commerce', { ...auth, data: { provider: 'shopify', url: 'https://demo.myshopify.com', token: 'commerce-browser-token', read_only: true } });
  expect(connected.status()).toBe(201);
  const store = await connected.json();
  expect((await request.post('/api/backend/commerce/' + store.id + '/test', auth)).ok()).toBeTruthy();
  const base = '/api/backend/chat/pub_order_proof_demo/widget';
  let conversation: string | undefined;
  async function turn(message: string, target?: string) {
    const response = await request.post(base + '/turns', { headers: { Origin: 'http://localhost:3000' }, data: { message, request_id: crypto.randomUUID(), ...(target ?? conversation ? { conversation_id: target ?? conversation } : {}) } });
    expect(response.status()).toBe(202);
    const queued = await response.json();
    if (!target) conversation = queued.conversation_id;
    const result = await (await request.get(base + '/turns/' + queued.id)).json();
    expect(result.state).toBe('complete');
    return result;
  }
  const pending = await turn('ORDER DEMO');
  expect(pending.answer).toContain('one-time code');
  expect(pending.answer).not.toContain('Private commerce item');
  const reference = pending.answer.match(/ORDER ([a-f0-9]{12})/)![1];
  const { code } = await (await request.get('/api/backend/_test/order-code')).json();
  const other = await request.post(base + '/turns', { headers: { Origin: 'http://localhost:3000' }, data: { message: 'Hello', request_id: crypto.randomUUID() } });
  const foreign = (await other.json()).conversation_id;
  expect((await turn('ORDER ' + reference + ' ' + code, foreign)).answer).not.toContain('Private commerce item');
  const verified = await turn('ORDER ' + reference + ' ' + code);
  expect(verified.answer).toContain('Private commerce item');
  expect((await turn('ORDER ' + reference + ' ' + code)).answer).not.toContain('Private commerce item');
  const transcript = await (await request.get('/api/backend/conversations/' + conversation + '/messages', auth)).json();
  expect(transcript.some((m: { role: string; content: string }) => m.role === 'user' && m.content.includes('[redacted]'))).toBe(true);
  expect(transcript.filter((m: { role: string }) => m.role === 'user').some((m: { content: string }) => m.content.includes(code))).toBe(false);
  expect((await request.delete('/api/backend/commerce/' + store.id, auth)).status()).toBe(204);
});
