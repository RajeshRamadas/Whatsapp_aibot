import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
test('Hindi operational catalogs support mobile controls, recovery and unchanged technical values', async ({ page }) => {
  test.setTimeout(60000);
  await page.context().addCookies([{ name: 'chatbot_locale', value: 'hi', url: 'http://localhost:3000' }]);
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'language@example.com', password: 'language-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.setViewportSize({ width: 320, height: 1000 });
  for (const [path, title, loaded] of [
    ['billing', 'बिलिंग', 'प्लान की तुलना करें'],
    ['analytics', 'विश्लेषण', 'फ़िल्टर लागू करें'],
    ['campaigns', 'अभियान', 'नया अभियान'],
    ['leads', 'संभावित ग्राहक', 'CSV एक्सपोर्ट करें'],
  ]) {
    await page.goto('/dashboard/' + path);
    await expect(page.getByRole('heading', { name: title, level: 1, exact: true })).toBeVisible();
    await expect(page.getByText(loaded, { exact: true }).first()).toBeVisible();
    if (path === 'billing') await expect(page.getByRole('button', { name: 'बिलिंग का विवरण सहेजें', exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), path).toBeTruthy();
    expect((await new AxeBuilder({ page }).analyze()).violations, path).toEqual([]);
  }
  await page.goto('/dashboard/campaigns');
  await page.getByRole('button', { name: 'संपर्क और समूह', exact: true }).click();
  const sample = await page.getByRole('link', { name: 'CSV का उदाहरण डाउनलोड करें', exact: true }).getAttribute('href');
  expect(decodeURIComponent(sample || '')).toContain('phone,name,language,attributes,opt_in_at,opt_in_source');
  let fail = true;
  await page.route('**/api/backend/websites?*', route => fail
    ? route.fulfill({ status: 503, json: {} }) : route.continue());
  await page.goto('/dashboard/knowledge');
  await page.getByRole('button', { name: 'वेबसाइटें', exact: true }).click();
  await expect(page.getByRole('region', { name: 'वेबसाइटें', exact: true }).getByRole('alert'))
    .toContainText('वेबसाइटें लोड नहीं हो सकीं। फिर कोशिश करें।');
  fail = false;
  await page.getByRole('button', { name: 'फिर कोशिश करें', exact: true }).click();
  await expect(page.getByText('अभी कोई वेबसाइट नहीं है। अपने सहायक को सिखाने के लिए सार्वजनिक पृष्ठ जोड़ें।')).toBeVisible();
  const url = page.getByRole('textbox', { name: 'शुरुआती URL', exact: true });
  await url.fill('https://example.com/SKU-42?price=1000');
  await page.getByRole('button', { name: 'English', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Starting URL', exact: true })).toHaveValue('https://example.com/SKU-42?price=1000');
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await expect(url).toHaveValue('https://example.com/SKU-42?price=1000');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: 'test-results/hindi-operations.png', fullPage: true });
  await page.getByRole('button', { name: 'जवाब सुधारें', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'जवाब सुधारें', exact: true })).toBeVisible();
  await expect(page.getByText('इन फ़िल्टर से कोई जवाब नहीं मिला।')).toBeVisible();
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'platform@example.com', password: 'platform-browser-passphrase',
  } });
  await page.goto('/platform');
  await expect(page.getByRole('heading', { name: 'प्लैटफ़ॉर्म प्रशासन', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'वेबहुक की विफलताएँ', exact: true })).toBeVisible();
});
