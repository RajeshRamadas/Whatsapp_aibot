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
  const input = screen.getByLabelText('Image, PDF or audio');
  expect(input.getAttribute('accept')).toContain('audio/wav');
  fireEvent.change(input, { target: { files: [new File(['text'], 'file.txt', { type: 'text/plain' })] } });
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Choose a JPEG/PNG image, PDF, or AAC/M4A/WAV audio file.');
  expect(queueMedia).not.toHaveBeenCalled();
});

it('confirms Meta recipient and sends a supported file with no caption', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(queueMedia).mockResolvedValue({ status: 'queued', message_id: 1 });
  const onQueued = vi.fn();
  render(createElement(InboxMedia, { conversationId: 'one', recipient: 'customer', meta: true, onQueued }));
  const file = new File(['%PDF-1.7'], 'file.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Image, PDF or audio'), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  expect((await screen.findByRole('status')).textContent).toContain('Attachment queued.');
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('file.pdf\ncustomer'));
  expect(queueMedia).toHaveBeenCalledWith('one', file, '', expect.any(String));
  expect(onQueued).toHaveBeenCalledOnce();
});

it.each([{ mime: 'audio/wav', name: 'reply.wav' }, { mime: 'video/mp4', name: 'reply.mp4' }])(
  'previews $mime locally, sends only after confirmation and releases the preview', async ({ mime, name }) => {
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
  fireEvent.change(screen.getByLabelText('Image, PDF or audio'), { target: { files: [file] } });
  expect((await screen.findByLabelText('Preview audio before sending')).getAttribute('src')).toBe('blob:private-audio');
  expect(queueMedia).not.toHaveBeenCalled();
  fireEvent.submit(screen.getByRole('form', { name: 'Send an attachment' }));
  await screen.findByRole('status');
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining(name + '\ncustomer'));
  expect(queueMedia).toHaveBeenCalledWith('one', file, '', expect.any(String));
  await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:private-audio'));
  expect(screen.queryByLabelText('Preview audio before sending')).toBeNull();
});
