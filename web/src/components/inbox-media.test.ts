import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { InboxMedia, MessageMedia } from './inbox-media';
import { queueMedia } from '@/api/media';
vi.mock('@/api/media', () => ({ queueMedia: vi.fn(), mediaUrl: () => '/private/attachment' }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('keeps a failed upload available for a retry and shows the server error', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(queueMedia).mockRejectedValue(new Error('Workspace upload limit reached'));
  render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', onQueued: vi.fn() }));
  const file = new File(['%PDF-1.7'], 'file.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Image or document'), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Workspace upload limit reached');
  expect((screen.getByRole('button', { name: 'Send attachment' }) as HTMLButtonElement).disabled).toBe(false);
});

it('renders expired and long voice-note states without a downloadable link', () => {
  render(createElement(MessageMedia, { conversationId: 'one', attachments: [{
    id: 'voice', kind: 'audio', status: 'expired', interpretation: 'too_long', size: 1,
  }] }));
  expect(screen.getByText('Attachment is unavailable or expired.')).toBeTruthy();
  expect(screen.getByText('Voice notes over five minutes are sent to an agent without transcription.')).toBeTruthy();
  expect(screen.queryByRole('link')).toBeNull();
});

it('limits Meta attachments to supported files and omits captions', async () => {
  vi.mocked(queueMedia).mockClear();
  render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', meta: true, onQueued: vi.fn() }));
  expect(screen.queryByLabelText('Attachment caption (optional)')).toBeNull();
  const input = screen.getByLabelText('Image, PDF, audio or video');
  expect(input.getAttribute('accept')).toContain('audio/wav');
  expect(input.getAttribute('accept')).toContain('image/gif');
  fireEvent.change(input, { target: { files: [new File(['text'], 'file.txt', { type: 'text/plain' })] } });
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Choose a JPEG, PNG or GIF image, PDF, AAC/M4A/WAV audio or MP4 video file.');
  expect(queueMedia).not.toHaveBeenCalled();
});

it('confirms Meta recipient and sends a supported file with no caption', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(queueMedia).mockResolvedValue({ status: 'queued', message_id: 1 });
  const onQueued = vi.fn();
  render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', meta: true, onQueued }));
  const file = new File(['%PDF-1.7'], 'file.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Image, PDF, audio or video'), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  expect((await screen.findByRole('status')).textContent).toContain('Attachment queued.');
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('file.pdf\ncustomer'));
  expect(queueMedia).toHaveBeenCalledWith('one', file, '', expect.any(String));
  expect(onQueued).toHaveBeenCalledOnce();
});

it.each([{ mime: 'audio/wav', name: 'reply.wav', label: 'Preview audio before sending' },
  { mime: 'video/mp4', name: 'reply.mp4', label: 'Preview video before sending' }])(
  'previews $mime locally, sends only after confirmation and releases the preview', async ({ mime, name, label }) => {
  const revoke = vi.fn(); const OriginalURL = URL;
  vi.stubGlobal('URL', class extends OriginalURL {
    static createObjectURL = vi.fn(() => 'blob:private-audio');
    static revokeObjectURL = revoke;
  });
  vi.mocked(queueMedia).mockClear();
  vi.mocked(queueMedia).mockResolvedValue({ status: 'queued', message_id: 2 });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', meta: true, onQueued: vi.fn() }));
  const file = new File(['fixture'], name, { type: mime });
  fireEvent.change(screen.getByLabelText('Image, PDF, audio or video'), { target: { files: [file] } });
  expect((await screen.findByLabelText(label)).getAttribute('src')).toBe('blob:private-audio');
  expect(queueMedia).not.toHaveBeenCalled();
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  await screen.findByRole('status');
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining(name + '\ncustomer'));
  expect(queueMedia).toHaveBeenCalledWith('one', file, '', expect.any(String));
  await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:private-audio'));
  expect(screen.queryByLabelText(label)).toBeNull();
});

it('releases video previews on replacement and unmount without uploading', async () => {
  const revoke = vi.fn(); const OriginalURL = URL;
  vi.stubGlobal('URL', class extends OriginalURL {
    static createObjectURL = vi.fn().mockReturnValueOnce('blob:first').mockReturnValueOnce('blob:second');
    static revokeObjectURL = revoke;
  });
  vi.mocked(queueMedia).mockClear();
  const view = render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', meta: true, onQueued: vi.fn() }));
  const input = screen.getByLabelText('Image, PDF, audio or video');
  for (const name of ['first', 'second']) {
    fireEvent.change(input, { target: { files: [new File(['video'], name + '.mp4', { type: 'video/mp4' })] } });
    expect((await screen.findByLabelText('Preview video before sending')).getAttribute('src')).toBe('blob:' + name);
  }
  expect(revoke).toHaveBeenCalledWith('blob:first');
  view.unmount();
  expect(revoke).toHaveBeenCalledWith('blob:second');
  expect(queueMedia).not.toHaveBeenCalled();
});

it('plays ready videos privately and leaves failed videos unavailable', () => {
  render(createElement(MessageMedia, { conversationId: 'one', attachments: [
    { id: 'video', kind: 'video', status: 'ready', mime_type: 'video/mp4', filename: 'reply.mp4', size: 100 },
    { id: 'failed', kind: 'video', status: 'failed', size: 0 },
  ] }));
  expect(screen.getByLabelText('Video attachment').getAttribute('src')).toBe('/private/attachment');
  expect(screen.getByLabelText('Video attachment').hasAttribute('autoplay')).toBe(false);
  expect(screen.getAllByRole('link')).toHaveLength(1);
  expect(screen.getByText('Attachment is unavailable or expired.')).toBeTruthy();
});

it('plays and stops an animated GIF on demand', () => {
  render(createElement(MessageMedia, { conversationId: 'one', attachments: [
    { id: 'gif', kind: 'image', status: 'ready', mime_type: 'image/gif', filename: 'reply.gif', size: 100 },
  ] }));
  expect(screen.queryByRole('img')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Play GIF' }));
  expect(screen.getByRole('img').getAttribute('src')).toBe('/private/attachment');
  fireEvent.click(screen.getByRole('button', { name: 'Stop GIF' }));
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.getByRole('link').getAttribute('href')).toBe('/private/attachment');
});
