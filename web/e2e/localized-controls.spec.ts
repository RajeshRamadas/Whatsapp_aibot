import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
test('Hindi notices, widget controls and offline inbox keep customer data separate', async ({ page }) => {
  await page.context().addCookies([{ name: 'chatbot_locale', value: 'hi', url: 'http://localhost:3000' }]);
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'language@example.com', password: 'language-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/bot');
  await expect(page.getByLabel('बॉट का नाम', { exact: true })).toBeVisible();
  const original = await (await page.request.get('/api/backend/tenant')).json();
  await page.getByText('ग्राहकों के लिए डिफ़ॉल्ट संदेश', { exact: true }).click();
  await page.getByRole('combobox', { name: 'संदेश की भाषा', exact: true }).selectOption('hi');
  const greeting = 'नमस्ते {bot}, {business} में आपका स्वागत है।';
  await page.getByLabel('अभिवादन', { exact: true }).fill(greeting);
  await page.getByRole('button', { name: 'संदेश सहेजें', exact: true }).click();
  await expect(page.getByText('संदेश सहेजे गए।', { exact: true })).toBeVisible();
  const saved = await (await page.request.get('/api/backend/bot/messages')).json();
  expect(saved.overrides.hi.greeting).toBe(greeting);
  expect((await (await page.request.get('/api/backend/tenant')).json()).default_language).toBe(original.default_language);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'डिफ़ॉल्ट संदेश बहाल करें', exact: true }).click();
  await expect(page.getByText('संदेश सहेजे गए।', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: 'test-results/hindi-notices.png', fullPage: true });
  await page.goto('/dashboard');
  await page.getByText('वेबसाइट विजेट', { exact: true }).click();
  const widgetGreeting = page.getByRole('textbox', { name: 'अभिवादन', exact: true });
  await expect(widgetGreeting).toBeVisible();
  const customerGreeting = await widgetGreeting.inputValue();
  await widgetGreeting.fill('Customer SKU-42 मूल अभिवादन');
  await page.getByRole('button', { name: 'English', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Greeting', exact: true })).toHaveValue('Customer SKU-42 मूल अभिवादन');
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await widgetGreeting.fill(customerGreeting);
  await page.goto('/dashboard/inbox');
  await expect(page.getByRole('heading', { name: 'इनबॉक्स', exact: true })).toBeVisible();
  await expect(page.getByText('कोई बातचीत नहीं मिली।', { exact: true })).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    await new Promise<void>(resolve => {
      if (navigator.serviceWorker.controller) resolve();
      else navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true });
    });
  });
  await expect.poll(async () => page.evaluate(async () => {
    const snapshot = await caches.match('/dashboard/inbox-offline-snapshot');
    return snapshot ? await snapshot.text() : '';
  })).toContain('ऑफ़लाइन इनबॉक्स');
  await page.context().setOffline(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'ऑफ़लाइन इनबॉक्स', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi');
  await page.context().setOffline(false);
  await page.goto('/missing-interface-page');
  await expect(page.getByRole('heading', { name: 'पृष्ठ नहीं मिला', exact: true })).toBeVisible();
});
