import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('Hindi interface persists, preserves drafts and protects private screens', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'लॉग इन करें', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi');
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'language@example.com', password: 'language-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/bot');
  await expect(page.getByRole('heading', { name: 'बॉट की सेटिंग', exact: true, level: 1 })).toBeVisible();
  const original = (await (await page.request.get('/api/backend/tenant')).json());
  await page.getByLabel('बॉट का नाम', { exact: true }).fill('Draft SKU-42');
  await page.getByRole('button', { name: 'English', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByLabel('Bot name', { exact: true })).toHaveValue('Draft SKU-42');
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await expect(page.getByLabel('बॉट का नाम', { exact: true })).toHaveValue('Draft SKU-42');
  await page.getByLabel('बॉट का नाम', { exact: true }).fill(original.bot_name);
  expect((await (await page.request.get('/api/backend/tenant')).json()).default_language).toBe(original.default_language);
  await page.goto('/dashboard/knowledge');
  await page.getByRole('button', { name: 'स्वीकृत प्रश्नोत्तर', exact: true }).click();
  await page.getByLabel('प्रश्न', { exact: true }).fill('मेरा SKU-42 कैसे लौटाएँ?');
  await page.getByRole('button', { name: 'English', exact: true }).click();
  await expect(page.getByLabel('Question', { exact: true })).toHaveValue('मेरा SKU-42 कैसे लौटाएँ?');
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await expect(page.getByLabel('प्रश्न', { exact: true })).toHaveValue('मेरा SKU-42 कैसे लौटाएँ?');
  await page.setViewportSize({ width: 320, height: 1000 });
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: 'test-results/hindi-core.png', fullPage: true });
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi');
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'language-reader@example.com', password: 'language-browser-passphrase',
  } });
  await page.goto('/dashboard/knowledge');
  await expect(page.getByText('केवल कार्यक्षेत्र के मालिक और एडमिन जानकारी की फ़ाइलें सँभाल सकते हैं।')).toBeVisible();
  expect((await page.request.get('/api/backend/bot/messages')).status()).toBe(403);
  await page.context().addCookies([{ name: 'error_boundary_fixture', value: '1', url: 'http://localhost:3000' }]);
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'यह पृष्ठ लोड नहीं हो सका', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi');
});

test('unknown interface locale safely falls back to English', async ({ page }) => {
  await page.context().addCookies([{ name: 'chatbot_locale', value: '..%2Fhi', url: 'http://localhost:3000' }]);
  await page.goto('/login');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByRole('heading', { name: 'Log in', exact: true })).toBeVisible();
});
