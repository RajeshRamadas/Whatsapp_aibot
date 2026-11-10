import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('scans become searchable knowledge with page citations and useful failure states', async ({ page }) => {
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'ocr@example.com', password: 'ocr-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.goto('/dashboard/knowledge');
  await expect(page.getByText('No files yet. Upload a document to get started.', { exact: true })).toBeVisible();
  await expect(page.getByText(/Scanned PDFs and PNG, JPEG, TIFF or WebP/)).toBeVisible();
  await page.getByLabel('Choose files', { exact: true }).setInputFiles('/tmp/chatbot-ocr-fixture.png');
  const source = page.getByRole('listitem').filter({ hasText: 'chatbot-ocr-fixture.png' });
  await expect(source.getByText('Ready', { exact: true })).toBeVisible();
  const cookies = await page.context().cookies();
  const csrf = cookies.find(cookie => cookie.name === 'chatbot_csrf')?.value ?? '';
  const preview = await page.request.post('/api/backend/bot/test', { data: { message: 'What is the refund policy?' },
    headers: { 'X-CSRF-Token': csrf } });
  expect(preview.ok()).toBeTruthy();
  expect((await preview.json()).sources).toContainEqual({ number: 1, filename: 'chatbot-ocr-fixture.png', heading: null, page: 1 });
  await page.getByLabel('Choose files', { exact: true }).setInputFiles({ name: 'broken.png', mimeType: 'image/png',
    buffer: Buffer.from('not an image') });
  await expect(page.getByRole('listitem').filter({ hasText: 'broken.png' }).getByRole('alert'))
    .toContainText('Try a clearer scan or a smaller document.');
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: 'test-results/knowledge-ocr.png', fullPage: true });
  await source.getByRole('button', { name: 'Delete', exact: true }).click();
  await source.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(source).toHaveCount(0);
  const broken = page.getByRole('listitem').filter({ hasText: 'broken.png' });
  await broken.getByRole('button', { name: 'Delete', exact: true }).click();
  await broken.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(broken).toHaveCount(0);
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'ocr-reader@example.com', password: 'ocr-browser-passphrase',
  } });
  await page.goto('/dashboard/knowledge');
  await expect(page.getByText('Only workspace owners and admins can manage knowledge files.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Choose files' })).toHaveCount(0);
});
