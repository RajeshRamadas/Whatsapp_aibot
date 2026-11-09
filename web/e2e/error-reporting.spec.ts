import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('fatal errors show a private, accessible recovery screen', async ({ page, context }) => {
  expect((await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  })).ok()).toBeTruthy();
  await context.addCookies([{ name: 'error_boundary_fixture', value: '1', url: 'http://localhost:3000' }]);
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'We couldn’t load this page' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByText(/Cannot read properties/)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: '../docs/screenshots/t8-3-error-recovery.png', fullPage: true });
});
