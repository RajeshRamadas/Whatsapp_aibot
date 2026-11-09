import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('viewer reviews analytics and filters the daily report by channel', async ({ page }) => {
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'analytics@example.com', password: 'analytics-browser-passphrase' },
  });
  expect(login.ok()).toBeTruthy();
  await page.goto('/dashboard/analytics');
  await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible();
  const metric = (name: string) => page.getByText(name, { exact: true }).first().locator('..').locator('dd');
  await expect(metric('Conversations')).toHaveText('2');
  await expect(metric('Messages')).toHaveText('4');
  await expect(metric('Answered by bot')).toHaveText('50%');
  await expect(metric('Handed to a person')).toHaveText('50%');
  await expect(metric('Average first response')).toHaveText('20 seconds');
  await expect(metric('Leads captured')).toHaveText('1');
  await expect(page.getByRole('table', { name: 'Daily activity' }).locator('tbody tr')).toHaveCount(2);
  await expect(page.getByText('First lead captured', { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.screenshot({ path: '../docs/screenshots/t8-2-analytics.png', fullPage: true });
  await page.getByLabel('Channel').selectOption('whatsapp');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(metric('Conversations')).toHaveText('1');
  await expect(metric('Handed to a person')).toHaveText('100%');
  await expect(metric('Average first response')).toHaveText('30 seconds');
  await expect(page.getByRole('table', { name: 'Daily activity' }).locator('tbody tr')).toHaveCount(1);
  await page.getByLabel('From date').fill('2020-01-01');
  await page.getByLabel('To date').fill('2020-01-02');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(page.getByText('No customer activity in this date range.')).toBeVisible();
});
