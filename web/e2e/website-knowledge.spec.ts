import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('website knowledge: crawl, cite, refresh, schedule, remove and protect reader access', async ({ page }) => {
  await page.goto('/login');
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'websites@example.com', password: 'websites-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/knowledge');
  await page.getByRole('button', { name: 'Websites', exact: true }).click();
  await expect(page.getByText('No websites yet. Add a public page to teach your assistant.')).toBeVisible();
  await page.getByLabel('Starting URL').fill('https://example.com/knowledge');
  await page.getByText('Follow links on the same host', { exact: true }).click();
  await page.getByLabel('Maximum pages').fill('3');
  await page.getByRole('button', { name: 'Add website', exact: true }).click();
  const site = page.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'https://example.com/knowledge', exact: true }) });
  await expect(site.getByText('Ready', { exact: true })).toBeVisible();
  await expect(site.getByText('Indexed pages: 2 / 3', { exact: true })).toBeVisible();
  await site.getByRole('button', { name: 'Show indexed pages', exact: true }).click();
  await expect(site.getByRole('link', { name: 'https://example.com/knowledge/refunds', exact: true })).toBeVisible();
  const cookies = await page.context().cookies();
  const csrf = cookies.find(cookie => cookie.name === 'chatbot_csrf')?.value ?? '';
  const preview = await page.request.post('/api/backend/bot/test', { data: { message: 'What is the refund policy?' },
    headers: { 'X-CSRF-Token': csrf } });
  expect(preview.ok()).toBeTruthy();
  expect((await preview.json()).sources.some((source: { filename: string }) =>
    source.filename === 'https://example.com/knowledge/refunds')).toBeTruthy();
  const scheduled = page.waitForResponse(response => response.url().includes('/websites/') && response.request().method() === 'PATCH');
  await site.getByLabel('Refresh schedule').selectOption('weekly');
  expect((await scheduled).ok()).toBeTruthy();
  await expect(site.getByText(/Next refresh \(IST\):/)).not.toContainText('No scheduled refresh');
  const refreshed = page.waitForResponse(response => response.url().endsWith('/refresh') && response.request().method() === 'POST');
  await site.getByRole('button', { name: 'Refresh now', exact: true }).click();
  expect((await refreshed).ok()).toBeTruthy();
  await expect(site.getByText('Ready', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.screenshot({ path: 'test-results/website-knowledge.png', fullPage: true });
  await site.getByRole('button', { name: 'Delete website', exact: true }).click();
  await site.getByRole('button', { name: 'Confirm deletion', exact: true }).click();
  await expect(site).toHaveCount(0);
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'websites-reader@example.com', password: 'websites-browser-passphrase',
  } });
  await page.goto('/dashboard/knowledge');
  await expect(page.getByText('Only workspace owners and admins can manage knowledge files.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Websites', exact: true })).toHaveCount(0);
});
