import { expect, test } from '@playwright/test';

test('agent handles an inbound conversation with live transcript updates', async ({ page }) => {
  const conversation = '15550001234';
  const login = await page.request.post('/api/backend/auth/login', {
    data: { email: 'inbox@example.com', password: 'inbox-test-passphrase-392' },
  });
  expect(login.ok()).toBeTruthy();
  const workspaces = await page.request.get('/api/backend/workspaces');
  expect(workspaces.ok()).toBeTruthy();
  const [workspace] = await workspaces.json();
  await page.context().addCookies([{ name: 'active_workspace', value: workspace.tenant_id,
    url: 'http://localhost:3000' }]);
  await page.goto('/dashboard/inbox');
  await page.getByRole('button', { name: new RegExp(conversation) }).click();
  await page.getByRole('button', { name: 'Take over' }).click();
  await expect(page.getByRole('button', { name: 'Send reply' })).toBeDisabled();
  const reply = 'Inbox browser test ' + Date.now();
  await page.getByRole('textbox', { name: 'Reply or internal note' }).fill(reply);
  await page.getByRole('button', { name: 'Send reply' }).click();
  await expect(page.getByRole('list', { name: 'Messages' }).getByText(reply)).toBeVisible();

  // Use a separate request to simulate another agent adding to the transcript.
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'chatbot_csrf');
  const path = await page.evaluate(() => performance.getEntriesByType('resource')
    .map(entry => entry.name).find(url => /\/conversations\/[^/]+\/messages$/.test(url)));
  expect(path).toBeTruthy();
  const id = new URL(path!).pathname.split('/').at(-2);
  const note = 'Live inbox update ' + Date.now();
  const response = await page.request.post('/api/backend/inbox/conversations/' + id + '/note', {
    headers: { 'X-CSRF-Token': csrf?.value ?? '' },
    data: { text: note },
  });
  expect(response.ok()).toBeTruthy();
  await expect(page.getByRole('list', { name: 'Messages' }).getByText(note)).toBeVisible();
  await page.getByRole('button', { name: 'Hand back to bot' }).click();
  await expect(page.getByRole('button', { name: new RegExp(conversation + '.*bot') })).toHaveCount(1);
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reopen' })).toBeVisible();
});
