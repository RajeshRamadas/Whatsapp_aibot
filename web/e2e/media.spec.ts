import { createHmac } from 'node:crypto';
import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('voice transcript, private downloads and confirmed agent attachments work in the inbox', async ({ page }) => {
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'media@example.com', password: 'media-browser-passphrase',
  } })).ok()).toBeTruthy();
  const body = JSON.stringify({ entry: [{ changes: [{ value: { metadata: { phone_number_id: 'MEDIA_BROWSER' },
    messages: [{ id: 'wamid.media.browser', from: '15550001111', type: 'audio', audio: { id: '9001' } }],
  } }] }] });
  const inbound = await page.request.post((process.env.API_INTERNAL_URL ?? 'http://127.0.0.1:8000') + '/webhooks/whatsapp', {
    data: body, headers: { 'Content-Type': 'application/json',
      'X-Hub-Signature-256': 'sha256=' + createHmac('sha256', 'fixture').update(body).digest('hex') },
  });
  expect(inbound.ok()).toBeTruthy();
  await page.goto('/dashboard/inbox');
  await page.getByRole('button', { name: /15550001111/ }).click();
  await expect(page.getByRole('heading', { name: 'Voice transcript', exact: true })).toBeVisible();
  await expect(page.getByText('नमस्ते, आपके काम करने का समय क्या है?', { exact: true }).first()).toBeVisible();
  const url = await page.getByRole('link', { name: /Download attachment/ }).first().getAttribute('href');
  expect(url).toBeTruthy();
  const download = await page.request.get(url!);
  expect(download.ok()).toBeTruthy();
  expect((await download.body()).subarray(0, 4).toString()).toBe('RIFF');
  expect(download.headers()['cache-control']).toContain('no-store');
  const range = await page.request.get(url!, { headers: { Range: 'bytes=0-3' } });
  expect(range.status()).toBe(206);
  expect(range.headers()['content-range']).toMatch(/^bytes 0-3\//);
  const duration = await page.getByLabel('Voice note', { exact: true }).evaluate(async (audio: HTMLAudioElement) => {
    await audio.play(); audio.pause(); return audio.duration;
  });
  expect(duration).toBeCloseTo(1);
  await page.getByLabel('Image or document', { exact: true }).setInputFiles({ name: 'information.pdf',
    mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\nBrowser fixture') });
  await page.getByLabel('Attachment caption (optional)', { exact: true }).fill('Requested information');
  page.once('dialog', dialog => { expect(dialog.message()).toContain('information.pdf'); return dialog.accept(); });
  await page.getByRole('button', { name: 'Send attachment', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Attachment queued.' })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Messages' }).getByText('Requested information', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: /Download attachment: information.pdf/ })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: 'test-results/media-inbox.png', fullPage: true });
  await page.request.post('/api/backend/auth/login', { data: {
    email: 'media-reader@example.com', password: 'media-browser-passphrase',
  } });
  await page.goto('/dashboard/inbox');
  await page.getByRole('button', { name: /15550001111/ }).click();
  await expect(page.getByRole('heading', { name: 'Voice transcript', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send attachment', exact: true })).toHaveCount(0);
});
