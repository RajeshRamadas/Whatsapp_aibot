import { createHmac } from 'node:crypto';
import { expect, test } from '@playwright/test';

test('owner reviews usage, saves a policy, checks out and downloads the invoice', async ({ page, context }) => {
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  });
  expect(login.ok()).toBeTruthy();
  const workspaces = await page.request.get('/api/backend/workspaces');
  expect(workspaces.ok()).toBeTruthy();
  const [workspace] = await workspaces.json();
  await context.addCookies([{ name: 'active_workspace', value: workspace.tenant_id, url: 'http://localhost:3000' }]);
  await page.goto('/dashboard');
  await expect(page.getByText('AI messages: 8 of 10 this month', { exact: true })).toBeVisible();
  await expect(page.getByText('You’ve used at least 80% of your monthly AI messages.')).toBeVisible();
  await page.getByRole('link', { name: 'Compare plans' }).click();
  await expect(page.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible();
  await expect(page.getByText('AI messages: 8 of 10 this month', { exact: true })).toBeVisible();

  await page.getByRole('radio', { name: 'Continue with overage' }).check();
  await page.getByRole('button', { name: 'Save limit behaviour' }).click();
  await expect(page.getByText('Limit behaviour saved.')).toBeVisible();

  const profile = page.getByRole('region', { name: 'Billing details' });
  await profile.getByLabel('Legal name').fill('Browser Test Buyer');
  await profile.getByLabel('Billing address').fill('Test billing address');
  await profile.getByLabel('State or union territory').selectOption('27');
  await profile.getByLabel('PIN code').fill('400002');
  await profile.getByRole('button', { name: 'Save billing details' }).click();
  await expect(profile.getByText('Billing details saved.')).toBeVisible();

  await page.getByRole('radio', { name: 'Growth', exact: true }).check();
  await page.getByRole('button', { name: 'Start checkout', exact: true }).click();
  const checkout = page.getByRole('link', { name: 'Continue to Razorpay checkout' });
  await expect(checkout).toHaveAttribute('href', 'https://rzp.io/i/test');

  // Complete the simulated hosted payment through the real signed webhook.
  const body = JSON.stringify({ event: 'subscription.charged', payload: {
    subscription: { entity: { id: 'sub_test' } },
    payment: { entity: { id: 'pay_browser', amount: 707882, currency: 'INR', status: 'captured' } },
  } });
  const signature = createHmac('sha256', 'browser-test-webhook').update(body).digest('hex');
  const payment = await page.request.post('http://127.0.0.1:8000/webhooks/razorpay', {
    data: body, headers: { 'Content-Type': 'application/json', 'X-Razorpay-Signature': signature,
      'X-Razorpay-Event-Id': 'browser-test-charge' },
  });
  expect(payment.ok()).toBeTruthy();
  await page.getByRole('button', { name: 'Refresh status' }).click();
  await expect(page.getByText('Your subscription is active.')).toBeVisible();
  await expect(page.getByText('AI messages: 8 of 50 this month', { exact: true })).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await profile.getByRole('button', { name: 'Download PDF' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^invoice-.*\.pdf$/);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).subarray(0, 5).toString()).toBe('%PDF-');

  await page.getByRole('radio', { name: 'Starter', exact: true }).check();
  await expect(page.getByText(/On the lower plan: 10 ai messages per month/)).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Schedule downgrade' }).click();
  await expect(page.getByText('Plan change requested for the end of the billing cycle.')).toBeVisible();
  await expect(page.getByText('Current plan: Growth', { exact: true })).toBeVisible();
});
