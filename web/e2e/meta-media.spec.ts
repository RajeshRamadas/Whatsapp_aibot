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
    await expect(page.getByLabel('Attachment caption (optional)', { exact: true })).toHaveCount(0);
    const upload = page.getByLabel('Image, PDF or audio', { exact: true });
    await upload.setInputFiles({ name: 'unsupported.txt', mimeType: 'text/plain', buffer: Buffer.from('plain text') });
    await page.getByRole('button', { name: 'Send attachment', exact: true }).click();
    await expect(page.getByText('Choose a JPEG/PNG image, PDF, or AAC/M4A/WAV audio file.', { exact: true })).toBeVisible();
    const imageBytes = await (await page.request.get((await links.filter({ hasText: 'attachment.png' }).getAttribute('href'))!)).body();
    for (const [name, mimeType, buffer] of [['requested.pdf', 'application/pdf', Buffer.from('%PDF-1.7\nRequested file')],
      ['agent.png', 'image/png', imageBytes]] as const) {
      await upload.setInputFiles({ name, mimeType, buffer });
      page.once('dialog', dialog => { expect(dialog.message()).toContain(name); expect(dialog.message()).toContain('meta-test-customer'); return dialog.accept(); });
      await page.getByRole('button', { name: 'Send attachment', exact: true }).click();
      const saved = messages.getByRole('link', { name: 'Download attachment: ' + name, exact: true });
      await expect(saved).toBeVisible();
      expect((await page.request.get((await saved.getAttribute('href'))!)).ok()).toBeTruthy();
    }
    const audioBytes = await (await page.request.get((await messages.getByLabel('Voice note', { exact: true }).getAttribute('src'))!)).body();
    await upload.setInputFiles({ name: 'reply.wav', mimeType: 'audio/wav', buffer: audioBytes });
    const preview = page.getByLabel('Preview audio before sending', { exact: true });
    expect(await preview.getAttribute('src')).toMatch(/^blob:/);
    expect(await preview.evaluate(async (audio: HTMLAudioElement) => { audio.muted = true; await audio.play(); audio.pause(); return audio.duration; })).toBeCloseTo(1);
    page.once('dialog', dialog => { expect(dialog.message()).toContain('reply.wav'); return dialog.accept(); });
    await page.getByRole('button', { name: 'Send attachment', exact: true }).click();
    await expect(preview).toHaveCount(0);
    const sentAudio = messages.getByRole('link', { name: 'Download attachment: reply.wav', exact: true });
    await expect(sentAudio).toBeVisible();
    expect(await sentAudio.locator('..').getByLabel('Voice note', { exact: true }).evaluate(async (audio: HTMLAudioElement) => {
      audio.muted = true; await audio.play(); audio.pause(); return audio.duration;
    })).toBeCloseTo(1);
    expect((await (await page.request.get((await sentAudio.getAttribute('href'))!)).body()).subarray(0, 4).toString()).toBe('RIFF');
    await expect(messages.getByLabel('Voice note', { exact: true })).toHaveCount(2);
    await expect(messages.getByRole('heading', { name: 'Voice transcript', exact: true })).toHaveCount(0);
    await expect(messages.getByText('Sent', { exact: true })).toHaveCount(4);
    expect((await page.request.post('/api/backend/_test/meta-dm/receipt', { data: { kind, external_id: identity, status: 'read' } })).ok()).toBeTruthy();
    await expect(messages.getByText('Read', { exact: true })).toHaveCount(kind === 'instagram' ? 1 : 4);
    await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
    await expect(page.getByText('अटैचमेंट उपलब्ध नहीं है या इसकी अवधि खत्म हो गई है।', { exact: true })).toBeVisible();
    await expect(page.getByLabel('चित्र, PDF या ऑडियो', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'English', exact: true }).click();
    await expect(messages).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 320, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1280, height: 1000 });
    if (kind === 'instagram') {
      await messages.evaluate(node => { node.scrollTop = node.scrollHeight; });
      await page.screenshot({ path: '../docs/screenshots/t10-8-meta-agent-audio.png', fullPage: true });
    }
    const anonymous = await playwright.request.newContext({ baseURL: 'http://localhost:3000' });
    expect((await anonymous.get(url)).status()).toBe(401); await anonymous.dispose();
  }
});
