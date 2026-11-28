import { expect, test } from '@playwright/test';

test('embedded widget survives an offline send and reload', async ({ page, context }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  const host = 'http://localhost:3000/widget-fixture';
  const api = 'http://localhost:3000/api/v1/chat/fixture/widget';
  const bundle = await page.request.get((process.env.API_INTERNAL_URL ?? 'http://127.0.0.1:8000') + '/widget.js');
  expect(bundle.ok()).toBeTruthy();
  await page.route('http://localhost:3000/widget.js', route => route.fulfill({ response: bundle }));
  await page.addInitScript(() => Reflect.deleteProperty(window, 'EventSource'));
  await page.route(host, route => route.fulfill({
    contentType: 'text/html; charset=utf-8',
    body: '<!doctype html><meta charset="utf-8"><button>Host button</button><script async src="/widget.js" data-chatbot-key="fixture"></script>',
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
        answer: 'We open at nine. Pay: https://rzp.io/i/widget-demo. javascript:alert(1)', sources: [{ filename: 'Hours.pdf' }], feedback: null } });
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
  await page.evaluate(() => Object.defineProperty(navigator, 'onLine', { configurable: true, value: false }));
  await page.getByRole('textbox', { name: 'Ask a question' }).fill('Opening hours?');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('status'), browserErrors.join('\n')).toContainText('offline');
  expect(browserErrors).toEqual([]);
  expect(queued).toBe(0);
  await context.setOffline(false);
  await page.evaluate(() => {
    Reflect.deleteProperty(navigator, 'onLine');
    window.dispatchEvent(new Event('online'));
  });
  await expect(page.getByText('We open at nine.')).toBeVisible();
  expect(queued).toBe(1);
  await expect(page.getByRole('link', { name: 'https://rzp.io/i/widget-demo', exact: true })).toHaveAttribute('href', 'https://rzp.io/i/widget-demo');
  expect(await page.locator('a[href^="javascript:"]').count()).toBe(0);
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

test('widget opens approved booking and look links from replies', async ({ page, context }) => {
  const origin = 'http://localhost:3000';
  const api = origin + '/api/v1/chat/fixture/widget';
  const booking = 'https://beautiva.example/book';
  const look = 'https://beautiva.example/looks?mode=preview&lang=en';
  const bundle = await page.request.get((process.env.API_INTERNAL_URL ?? 'http://127.0.0.1:8000') + '/widget.js');
  expect(bundle.ok()).toBeTruthy();
  await page.route(origin + '/widget.js', route => route.fulfill({ response: bundle }));
  await page.route(origin + '/widget-fixture', route => route.fulfill({
    contentType: 'text/html; charset=utf-8',
    body: '<!doctype html><meta charset="utf-8"><script async src="/widget.js" data-chatbot-key="fixture" ' +
      `data-chatbot-links="${booking} ${look.replace('&', '&amp;')} http://beautiva.example/book"></script>`,
  }));
  await page.route(api + '/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/config')) {
      return route.fulfill({ json: { name: 'Beautiva assistant', color: '#064e3b', position: 'right', theme: 'light',
        icon: 'chat', greeting: '', show_greeting: false, avatar_url: '' } });
    }
    if (path.endsWith('/turns')) {
      return route.fulfill({ status: 202, json: { id: 'turn', conversation_id: 'conv', state: 'complete',
        answer: `Book at ${booking}. Try a look at (${look}). ` +
          'https://beautiva.example/other https://beautiva.example.evil.test/book ' +
          'https://beautiva.example@evil.test/book http://beautiva.example/book javascript:alert(1)',
        sources: [], feedback: null } });
    }
    return route.fulfill({ json: [{ id: 1, text: `Our team can help you book at ${booking}` }] });
  });
  await context.route('https://beautiva.example/**', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Beautiva test destination</title>',
  }));
  await page.goto(origin + '/widget-fixture');
  await page.getByRole('button', { name: 'Open chat' }).click();
  await expect(page.getByText('Beautiva assistant', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Ask a question' }).fill(`Can I use ${booking}?`);
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  const log = page.getByRole('log');
  const guest = log.getByRole('article').filter({ hasText: `Can I use ${booking}?` });
  const assistant = log.getByRole('article').filter({ hasText: 'Beautiva assistant' });
  const team = log.getByRole('article').filter({ hasText: 'Our team can help' });
  await expect(guest).toBeVisible();
  await expect(guest.getByRole('link')).toHaveCount(0);
  await expect(assistant.getByRole('link')).toHaveCount(2);
  await expect(team.getByRole('link', { name: booking, exact: true })).toHaveAttribute('href', booking);
  await expect(assistant).toContainText('https://beautiva.example.evil.test/book');
  for (const url of [booking, look]) {
    const link = assistant.getByRole('link', { name: url, exact: true });
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    const [destination] = await Promise.all([page.waitForEvent('popup'), link.click()]);
    await expect(destination).toHaveURL(url);
    await destination.close();
  }
});
