import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('owner enables a WhatsApp lead follow-up and can pause it', async ({ page }) => {
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'follow-ups@example.com', password: 'follow-up-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/leads');
  const panel = page.getByRole('region', { name: 'Lead follow-ups' });
  await expect(panel.getByText('Follow-ups paused', { exact: true })).toBeVisible();
  await panel.getByLabel('Approved WhatsApp template').selectOption({ label: 'salon_follow_up (en_US)' });
  await panel.getByLabel('Wait (hours)').fill('48');
  await panel.getByLabel('Template value 1').fill('Beautiva');
  await expect(panel.getByText('Need help booking at Beautiva?', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: 'Enable follow-ups', exact: true }).click();
  await expect(panel.getByText('Follow-ups enabled', { exact: true })).toBeVisible();
  await page.reload();
  await expect(panel.getByLabel('Wait (hours)')).toHaveValue('48');
  await expect(panel.getByLabel('Template value 1')).toHaveValue('Beautiva');
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 900 });
  await panel.screenshot({ path: '../docs/screenshots/t10-10-lead-follow-ups.png' });
  await panel.getByRole('button', { name: 'Pause follow-ups', exact: true }).click();
  await expect(panel.getByText('Follow-ups paused', { exact: true })).toBeVisible();
  await expect(panel.getByLabel('Template value 1')).toHaveValue('Beautiva');
});
