import { expect, test } from '@playwright/test';

test('install assets and workspace notification opt-in work', async ({ page, context }) => {
  await context.grantPermissions(['notifications']);
  await page.addInitScript(() => {
    const subscription = {
      endpoint: 'https://fcm.googleapis.com/fcm/send/browser-fixture',
      toJSON() {
        return { endpoint: this.endpoint, keys: { p256dh: 'B' + 'A'.repeat(86), auth: 'A'.repeat(22) } };
      },
    };
    Object.defineProperty(PushManager.prototype, 'getSubscription', {
      value: async () => sessionStorage.getItem('push-test') ? subscription : null,
    });
    Object.defineProperty(PushManager.prototype, 'subscribe', {
      value: async () => { sessionStorage.setItem('push-test', 'yes'); return subscription; },
    });
  });
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  });
  expect(login.ok()).toBeTruthy();
  const workspaces = await page.request.get('/api/backend/workspaces');
  const [workspace] = await workspaces.json();
  await context.addCookies([{ name: 'active_workspace', value: workspace.tenant_id,
    url: 'http://localhost:3000' }]);
  await page.goto('/dashboard/inbox');
  const enable = page.getByRole('button', { name: 'Enable notifications' });
  await expect(enable).toBeEnabled();
  await enable.click();
  await expect(page.getByRole('button', { name: 'Disable notifications' })).toBeEnabled();
  const endpoint = encodeURIComponent('https://fcm.googleapis.com/fcm/send/browser-fixture');
  const status = await page.request.get('/api/backend/inbox/push-subscription?endpoint=' + endpoint);
  expect(await status.json()).toEqual({ enabled: true });
  await page.getByRole('button', { name: 'Disable notifications' }).click();
  await expect(enable).toBeEnabled();
  const disabled = await page.request.get('/api/backend/inbox/push-subscription?endpoint=' + endpoint);
  expect(await disabled.json()).toEqual({ enabled: false });

  const manifest = await page.request.get('/inbox.webmanifest');
  const app = await manifest.json();
  expect(app.display).toBe('standalone');
  for (const icon of app.icons) {
    const response = await page.request.get(icon.src);
    expect(response.ok()).toBeTruthy();
    expect(response.headers()['content-type']).toContain('image/png');
  }
  await page.getByRole('button', { name: /15550001234/ }).click();
  await expect.poll(async () => page.evaluate(async () =>
    !!await caches.match('/dashboard/inbox-offline-snapshot'),
  )).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Offline inbox' })).toBeVisible();
  await expect(page.getByText('15550001234', { exact: false })).toBeVisible();
  await context.setOffline(false);
  await page.goto('/dashboard/inbox');
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect.poll(async () => page.evaluate(async () =>
    (await caches.keys()).includes('inbox-offline-v1'),
  )).toBe(false);
});
