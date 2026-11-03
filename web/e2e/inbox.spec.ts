import { expect, test } from '@playwright/test';

test('agent handles an inbound conversation with live transcript updates', async ({ page }) => {
  const conversation = process.env.INBOX_TEST_CONVERSATION;
  const storage = process.env.INBOX_TEST_STORAGE_STATE;
  test.skip(!conversation || !storage, 'Provide a seeded conversation and authenticated storage state');
  await page.context().addCookies(JSON.parse(await (await import('node:fs/promises')).readFile(storage!, 'utf8')).cookies);
  await page.goto('/dashboard/inbox');
  await page.getByRole('button', { name: new RegExp(conversation!) }).click();
  await page.getByRole('button', { name: 'Take over' }).click();
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
  await expect(page.getByRole('button', { name: new RegExp(conversation! + '.*bot') })).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reopen' })).toBeVisible();
});
