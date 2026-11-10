import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('improve answers: metrics, filters, correction, FAQ retrieval, dismissal and private access', async ({ page }) => {
  await page.goto('/login');
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'improve@example.com', password: 'improve-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/analytics');
  await page.getByRole('link', { name: 'Review top questions, feedback and unanswered replies' }).click();
  await expect(page.getByRole('heading', { name: 'Improve answers', exact: true })).toBeVisible();
  await expect(page.getByText('50%', { exact: true })).toBeVisible();
  await expect(page.getByText('Replies: 2', { exact: true })).toBeVisible();
  const original = page.getByRole('listitem').filter({ has: page.getByRole('heading', {
    name: 'What is the refund period?', exact: true,
  }) });
  await original.getByText('Bot reply', { exact: true }).click();
  await expect(original.getByText('Please ask our team.', { exact: true })).toBeVisible();
  await original.getByRole('button', { name: 'Add a Q&A answer', exact: true }).click();
  await expect(page.getByLabel('Q&A question', { exact: true })).toHaveValue('What is the refund period?');
  await page.getByLabel('Approved answer', { exact: true }).fill('Refunds are accepted within 14 days.');
  await page.getByRole('button', { name: 'Save Q&A', exact: true }).click();
  await expect(original).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Review status', exact: true }).selectOption('resolved');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(page.getByText('Q&A saved to workspace knowledge.', { exact: true })).toBeVisible();
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'chatbot_csrf')?.value ?? '';
  const preview = await page.request.post('/api/backend/bot/test', { data: { message: 'What is the refund period?' },
    headers: { 'X-CSRF-Token': csrf } });
  expect(preview.ok()).toBeTruthy();
  expect((await preview.json()).sources[0].filename).toBe('FAQ');
  await page.getByRole('button', { name: 'Curated Q&A', exact: true }).click();
  await expect(page.getByText('Refunds are accepted within 14 days.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Improve answers', exact: true }).click();
  const remaining = page.getByRole('listitem').filter({ has: page.getByRole('heading', {
    name: 'what is the refund period?', exact: true,
  }) });
  await remaining.getByRole('button', { name: 'Dismiss review', exact: true }).click();
  await expect(page.getByText('No replies match these filters.', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Review status', exact: true }).selectOption('all');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(remaining).toBeVisible();
  await page.setViewportSize({ width: 320, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.screenshot({ path: 'test-results/improve-answers.png', fullPage: true });
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'improve-reader@example.com', password: 'improve-browser-passphrase',
  } });
  await page.goto('/dashboard/knowledge?view=improve');
  await expect(page.getByText('Only workspace owners and admins can manage knowledge files.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Improve answers', exact: true })).toHaveCount(0);
  expect((await page.request.get('/api/backend/improvements')).status()).toBe(403);
});
