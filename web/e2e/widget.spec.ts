import { expect, test } from '@playwright/test';

test('embedded widget survives an offline send and reload', async ({ page, context }) => {
  const host = 'http://localhost:3000/widget-fixture';
  const api = 'http://127.0.0.1:8000/api/v1/chat/fixture/widget';
  await page.addInitScript(() => Reflect.deleteProperty(window, 'EventSource'));
  await page.route(host, route => route.fulfill({
    contentType: 'text/html',
    body: '<button>Host button</button><script async src="http://127.0.0.1:8000/widget.js" data-chatbot-key="fixture"></script>',
  }));
  let queued = 0;
  await page.route(api + '/**', async route => {
    const headers = { 'Access-Control-Allow-Origin': 'http://localhost:3000',
      'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type' };
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers });
      return;
    }
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/config')) {
      await route.fulfill({ headers, json: { name: 'Shop assistant', color: '#064e3b', position: 'left',
        theme: 'light', icon: 'chat', greeting: 'Welcome', show_greeting: true, avatar_url: '' } });
    } else if (path.endsWith('/turns')) {
      queued += 1;
      await route.fulfill({ status: 202, headers, json: { id: 'turn', conversation_id: 'conv', state: 'queued' } });
    } else if (path.endsWith('/turns/turn')) {
      await route.fulfill({ headers, json: { id: 'turn', conversation_id: 'conv', state: 'complete',
        answer: 'We open at nine.', sources: [{ filename: 'Hours.pdf' }], feedback: null } });
    } else if (path.endsWith('/feedback')) {
      await route.fulfill({ headers, json: { feedback: 'up' } });
    } else {
      await route.fulfill({ headers, json: [] });
    }
  });
  await page.goto(host);
  await page.getByRole('button', { name: 'Host button' }).click();
  await page.getByRole('button', { name: 'Open chat' }).click();
  await context.setOffline(true);
  await page.getByRole('textbox', { name: 'Ask a question' }).fill('Opening hours?');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText(/You’re offline/)).toBeVisible();
  expect(queued).toBe(0);
  await context.setOffline(false);
  await expect(page.getByText('We open at nine.')).toBeVisible();
  expect(queued).toBe(1);
  await page.getByText('Sources (1)', { exact: true }).click();
  await expect(page.getByText('Hours.pdf')).toBeVisible();
  await page.getByRole('button', { name: 'Helpful answer', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Helpful answer', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await page.getByRole('button', { name: 'Open chat' }).click();
  await expect(page.getByText('We open at nine.')).toBeVisible();
  await page.getByRole('button', { name: 'Start a new chat' }).click();
  await expect(page.getByText('We open at nine.')).toHaveCount(0);
});
