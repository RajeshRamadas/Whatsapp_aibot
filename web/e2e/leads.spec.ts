import { expect, test } from '@playwright/test';

test('lead status is saved and exported from the filtered board', async ({ page, context }) => {
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  });
  expect(login.ok()).toBeTruthy();
  const workspaces = await page.request.get('/api/backend/workspaces');
  expect(workspaces.ok()).toBeTruthy();
  const [workspace] = await workspaces.json();
  await context.addCookies([{ name: 'active_workspace', value: workspace.tenant_id, url: 'http://localhost:3000' }]);
  await page.goto('/dashboard/leads');
  const lead = page.getByRole('article').filter({ hasText: 'Asha Demo' });
  await lead.getByLabel('Status').selectOption('qualified');
  await lead.getByRole('button', { name: 'Save lead' }).click();
  await expect(lead.getByRole('button', { name: 'Save lead' })).toBeDisabled();
  await page.getByRole('button', { name: 'Board', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Qualified' }).getByText('Asha Demo')).toBeVisible();
  await page.getByLabel('Source', { exact: true }).selectOption('web');
  await expect(page.getByRole('region', { name: 'Qualified' }).getByText('Asha Demo')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('leads.csv');
  const stream = await download.createReadStream();
  let csv = '';
  for await (const chunk of stream) csv += chunk.toString();
  expect(csv).toContain('Asha Demo');
  expect(csv).toContain(',qualified,');
  await lead.getByRole('link', { name: 'Open conversation' }).click();
  await expect(page.getByRole('heading', { name: 'lead-browser-fixture' })).toBeVisible();
});
