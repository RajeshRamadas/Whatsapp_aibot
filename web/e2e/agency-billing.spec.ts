import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('owner links a client to consolidated billing and can unlink it', async ({ page }) => {
  const credentials = { email: `billing-${randomUUID()}@example.com`, password: 'agency-browser-passphrase' };
  expect((await page.request.post('/api/backend/auth/signup', { data: { ...credentials, name: 'Agency owner' } })).ok()).toBeTruthy();
  expect((await page.request.post('/api/backend/auth/login', { data: credentials })).ok()).toBeTruthy();
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'chatbot_csrf')!;
  for (const name of ['Agency Billing Fixture', 'Client Billing Fixture']) {
    expect((await page.request.post('/api/backend/workspaces', {
      headers: { 'X-CSRF-Token': csrf.value }, data: { name },
    })).ok()).toBeTruthy();
  }
  await page.goto('/dashboard/billing');
  const panel = page.getByRole('region', { name: 'Agency billing', exact: true });
  await panel.getByLabel('Client workspace', { exact: true }).selectOption({ label: 'Client Billing Fixture' });
  await panel.getByRole('button', { name: 'Link client workspace', exact: true }).click();
  await expect(panel.getByRole('list', { name: 'Billed workspaces' })).toContainText('Client Billing Fixture');
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).include('[aria-labelledby="agency-billing-heading"]').analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 900 });
  await panel.screenshot({ path: '../docs/screenshots/t10-9-billing.png' });
  page.once('dialog', dialog => dialog.accept());
  await panel.getByRole('button', { name: 'Unlink Client Billing Fixture' }).click();
  await expect(panel.getByText('No client workspaces linked.')).toBeVisible();
});
