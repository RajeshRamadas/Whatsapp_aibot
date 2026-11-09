import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('platform administrator reviews failures and suspends and resumes a workspace', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('platform@example.com');
  await page.getByLabel('Password', { exact: true }).fill('platform-browser-passphrase');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Platform administration', exact: true })).toBeVisible();
  const tenant = page.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'Platform Suspension Fixture' }) });
  await expect(tenant.getByText('Active', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Document ingestion', exact: true })).toBeVisible();
  await expect(page.getByText('private-document.pdf')).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  const rejected = await page.request.post('http://127.0.0.1:8000/webhooks/whatsapp', { data: {} });
  expect(rejected.status()).toBe(401);
  const webhooks = page.getByRole('region', { name: 'Webhook failures', exact: true });
  await webhooks.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(webhooks.getByText('HTTP status: 401', { exact: true }).first()).toBeVisible();

  page.once('dialog', dialog => dialog.accept());
  await tenant.getByRole('button', { name: 'Suspend workspace', exact: true }).click();
  await expect(tenant.getByText('Suspended', { exact: true })).toBeVisible();
  const denied = await page.request.post('/api/backend/auth/login', {
    data: { email: 'platform-member@example.com', password: 'platform-member-passphrase' },
  });
  expect(denied.status()).toBe(403);
  page.once('dialog', dialog => dialog.accept());
  await tenant.getByRole('button', { name: 'Resume workspace', exact: true }).click();
  await expect(tenant.getByText('Active', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: '../docs/screenshots/t7-5-platform-admin.png', fullPage: true });
  const allowed = await page.request.post('/api/backend/auth/login', {
    data: { email: 'platform-member@example.com', password: 'platform-member-passphrase' },
  });
  expect(allowed.ok()).toBeTruthy();
  expect((await page.request.get('/api/backend/platform/tenants')).status()).toBe(403);
  await page.goto('/platform');
  await expect(page.getByRole('heading', { name: 'Platform administration', exact: true })).toHaveCount(0);
});

test('platform lists show loading, empty and recoverable error states', async ({ page }) => {
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'platform@example.com', password: 'platform-browser-passphrase' },
  });
  expect(login.ok()).toBeTruthy();
  let release: () => void = () => {};
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/backend/platform/tenants?*', async route => {
    await waiting;
    await route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 25 } });
  });
  await page.route('**/api/backend/platform/failed-jobs?*', route => route.fulfill({
    status: 503, json: { error: { message: 'Unavailable' } },
  }));
  await page.goto('/platform');
  const tenants = page.getByRole('region', { name: 'Workspaces', exact: true });
  await expect(tenants.getByRole('status')).toHaveText('Loading records…');
  release();
  await expect(tenants.getByText('No records to show.')).toBeVisible();
  const jobs = page.getByRole('region', { name: 'Dead-letter jobs', exact: true });
  await expect(jobs.getByRole('alert')).toHaveText(/Could not load records/);
  await page.route('**/api/backend/platform/failed-jobs?*', route => route.fulfill({
    json: { items: [], total: 0, offset: 0, limit: 25 },
  }));
  await jobs.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(jobs.getByText('No records to show.')).toBeVisible();
});
