import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('Meta attachments stay private, appear in Inbox and hand off to staff', async ({ page, playwright }) => {
  test.setTimeout(90000); page.setDefaultTimeout(15000);
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'meta-media@example.com', password: 'meta-browser-passphrase',
  } })).ok()).toBeTruthy();
  const source = 'https://lookaside.fbsbx.com/ig_messaging_cdn/?signature=private-fixture';
  for (const [kind, identity] of [['instagram', '654310'], ['messenger', '654311']]) {
    await page.goto('/dashboard/channels');
    const form = page.getByRole('form', { name: 'Connect a Meta account', exact: true });
    await form.getByRole('combobox', { name: 'Channel', exact: true }).selectOption(kind);
    await form.getByLabel('Meta account or Page ID', { exact: true }).fill(identity);
    await form.getByLabel('Access token', { exact: true }).fill('synthetic-token');
    await form.getByRole('button', { name: 'Verify and connect', exact: true }).click();
    await page.getByRole('button', { name: 'Enable bot replies', exact: true }).last().click();
    await expect(page.getByText('Bot replies enabled for this account', { exact: true })).toHaveCount(kind === 'instagram' ? 1 : 2);
    expect((await page.request.post('/api/backend/_test/meta-dm/inbound', { data: {
      kind, external_id: identity, text: 'Please review these files', attachments: [
        { type: 'image', payload: { url: source } }, { type: 'audio', payload: { url: source } },
        { type: 'file', payload: { url: source } }, { type: 'file', payload: { url: source + '-unavailable' } },
      ],
    } })).ok()).toBeTruthy();
    await page.goto('/dashboard/inbox');
    await page.getByRole('combobox', { name: 'Channel', exact: true }).selectOption(kind);
    await page.getByRole('button', { name: /meta-test-customer/ }).click();
    const messages = page.getByRole('list', { name: 'Messages', exact: true });
    await expect(messages.getByText('Please review these files', { exact: true })).toBeVisible();
    await expect(messages.getByText('Thanks, our team will reply here as soon as someone is available.', { exact: true })).toBeVisible();
    await expect(messages.getByText('Attachment is unavailable or expired.', { exact: true })).toBeVisible();
    const image = messages.getByRole('img', { name: 'Customer or agent image attachment', exact: true });
    await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(240);
    await expect(messages.getByLabel('Voice note', { exact: true })).toBeVisible();
    const duration = await messages.getByLabel('Voice note', { exact: true }).evaluate(async (audio: HTMLAudioElement) => {
      audio.muted = true; await audio.play(); audio.pause(); return audio.duration;
    });
    expect(duration).toBeCloseTo(1);
    await expect(messages.getByRole('heading', { name: 'Voice transcript', exact: true })).toHaveCount(0);
    const links = messages.getByRole('link', { name: /Download attachment/ });
    await expect(links).toHaveCount(3);
    const url = (await links.filter({ hasText: 'attachment.pdf' }).getAttribute('href'))!;
    const download = await page.request.get(url);
    expect(download.ok()).toBeTruthy(); expect((await download.body()).toString()).toContain('%PDF-');
    expect(download.headers()['cache-control']).toContain('no-store');
    expect(await messages.innerHTML()).not.toContain('signature=private-fixture');
    await expect(page.getByRole('button', { name: 'Take over', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send attachment', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
    await expect(page.getByText('अटैचमेंट उपलब्ध नहीं है या इसकी अवधि खत्म हो गई है।', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'English', exact: true }).click();
    await expect(messages).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 320, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1280, height: 1000 });
    if (kind === 'instagram') await page.screenshot({ path: '../docs/screenshots/t10-8-meta-media.png', fullPage: true });
    const anonymous = await playwright.request.newContext({ baseURL: 'http://localhost:3000' });
    expect((await anonymous.get(url)).status()).toBe(401); await anonymous.dispose();
  }
});
