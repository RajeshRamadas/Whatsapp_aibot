import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('an owner verifies a domain and visitors see its agency branding', async ({ page }) => {
  const key = randomUUID();
  const credentials = { email: `brand-${key}@example.com`, password: 'agency-brand-passphrase' };
  const domain = `studio-${key.slice(0, 8)}.localhost`;
  expect((await page.request.post('/api/backend/auth/signup', { data: { ...credentials, name: 'Agency owner' } })).ok()).toBeTruthy();
  expect((await page.request.post('/api/backend/auth/login', { data: credentials })).ok()).toBeTruthy();
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'chatbot_csrf')!;
  expect((await page.request.post('/api/backend/workspaces', { headers: { 'X-CSRF-Token': csrf.value },
    data: { name: 'Brand Agency Fixture' } })).ok()).toBeTruthy();
  await page.goto('/dashboard/billing');
  const branding = page.getByRole('region', { name: 'Agency branding', exact: true });
  await branding.getByLabel('Brand name', { exact: true }).fill('Beautiva Studio');
  await branding.getByLabel('Custom domain', { exact: true }).fill(domain);
  await branding.getByRole('button', { name: 'Save branding', exact: true }).click();
  await expect(branding.getByText(`_chatbot-verification.${domain}`, { exact: true })).toBeVisible();
  await branding.getByRole('button', { name: 'Verify domain', exact: true }).click();
  await expect(branding.getByRole('status')).toHaveText('Domain verified.');
  const http = new URL(page.url());
  http.hostname = domain;
  http.pathname = '/login';
  await page.goto(http.toString());
  await expect(page).toHaveTitle('Beautiva Studio');
  await expect(page.getByText('Beautiva Studio', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('main').screenshot({ path: '../docs/screenshots/t10-9-branding.png' });
});
