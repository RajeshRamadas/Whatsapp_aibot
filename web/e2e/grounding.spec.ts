import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('grounding: cited delivery and unsupported draft becomes a staff handoff', async ({ page }) => {
  page.setDefaultTimeout(15000);
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'grounding@example.com', password: 'grounding-browser-passphrase',
  } })).ok()).toBeTruthy();
  const base = '/api/backend/chat/pub_grounding_demo/widget/turns';
  async function ask(message: string) {
    const id = crypto.randomUUID();
    expect((await page.request.post(base, { headers: { Origin: 'http://localhost:3000' },
      data: { request_id: id, message } })).status()).toBe(202);
    const result = await (await page.request.get(base + '/' + id)).json();
    expect(result.state).toBe('complete'); return result;
  }
  const supported = await ask('GROUNDING supported refund question');
  expect(supported.answer).toBe('Refunds accepted within 30 days. [1]');
  expect(supported.sources[0].filename).toBe('refund-policy.txt');
  const blocked = await ask('GROUNDING unsupported refund question');
  expect(blocked.answer).not.toContain('90 days'); expect(blocked.sources).toEqual([]);
  await page.goto('/dashboard/inbox');
  await page.getByRole('button').filter({ hasText: 'Waiting' }).last().click();
  const messages = page.getByRole('list', { name: 'Messages', exact: true });
  await expect(messages.getByText(blocked.answer, { exact: true })).toBeVisible();
  await expect(messages).not.toContainText('90 days');
  await expect(page.getByRole('button', { name: 'Take over', exact: true })).toBeVisible();
  await page.goto('/dashboard/knowledge?view=improve');
  const review = page.getByRole('listitem').filter({ has: page.getByRole('heading', {
    name: 'GROUNDING unsupported refund question', exact: true,
  }) });
  await expect(review).toBeVisible();
  await review.getByText('Bot reply', { exact: true }).click();
  await expect(review.getByText(blocked.answer, { exact: true })).toBeVisible();
  await expect(review).not.toContainText('90 days');
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 1000 }); await page.mouse.move(0, 0);
  await page.screenshot({ path: '../docs/screenshots/t10-5-grounding-handoff.png', fullPage: true });
});
