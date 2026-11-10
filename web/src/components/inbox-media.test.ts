import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { InboxMedia, MessageMedia } from './inbox-media';
import { queueMedia } from '@/api/media';
vi.mock('@/api/media', () => ({ queueMedia: vi.fn(), mediaUrl: () => '/private/attachment' }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
