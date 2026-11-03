import { expect, test } from '@playwright/test';

test('workspace admin connects through the Meta popup and requests a test message', async ({ page }) => {
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  });
  expect(login.ok()).toBeTruthy();
  const channel = { external_id: '200', display_name: 'Example Business', status: 'configured' };
  await page.route('**/api/backend/whatsapp/signup/config', route => route.fulfill({
    json: { enabled: true, app_id: '123', config_id: '456', version: 'v23.0', channel: null },
  }));
  await page.route('https://connect.facebook.net/en_US/sdk.js', route => route.fulfill({
    contentType: 'application/javascript',
    body: `window.FB = { init() {}, login(callback) {
      callback({ authResponse: { code: 'fake-code' } });
      for (const origin of ['https://example.com', 'https://www.facebook.com']) {
        window.dispatchEvent(new MessageEvent('message', { origin, data: JSON.stringify({
          type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH',
          data: { waba_id: '100', phone_number_id: origin.includes('facebook') ? '200' : '999' },
        }) }));
      }
    }};`,
  }));
  await page.route('**/api/backend/whatsapp/signup', async route => {
    expect(route.request().postDataJSON()).toEqual({
      code: 'fake-code', waba_id: '100', phone_number_id: '200', pin: '123456',
    });
    await route.fulfill({ json: channel });
  });
  await page.route('**/api/backend/whatsapp/signup/test-message', async route => {
    expect(route.request().postDataJSON()).toEqual({ recipient: '+15550001234' });
    await route.fulfill({ json: { status: 'accepted' } });
  });
  await page.goto('/dashboard');
  await page.getByLabel('Six-digit registration PIN').fill('123456');
  await page.getByRole('button', { name: 'Connect with Meta' }).click();
  await expect(page.getByText('WhatsApp connected.', { exact: true })).toBeVisible();
  await page.getByLabel('Your test recipient number, including country code').fill('+15550001234');
  await page.getByRole('button', { name: 'Send me a test message' }).click();
  await expect(page.getByText('Meta accepted the test message. Check WhatsApp for delivery.')).toBeVisible();
});
