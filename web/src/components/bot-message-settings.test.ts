import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import * as messages from '@/api/bot-messages';
import { BotMessageSettings } from './bot-message-settings';
vi.mock('@/api/bot-messages', () => ({ load: vi.fn(), save: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });
const defaults = { greeting: 'Hello {bot} at {business}', unknown: 'Unknown', handoff: 'Our team will help.',
  closed: 'Closed until {opening}', unsupported: 'Please type.', rate: 'Wait a minute.', error: 'Try again.' };
const catalog: messages.Catalog = { languages: { en: 'English', hi: 'हिन्दी' }, defaults: {
  en: defaults, hi: { ...defaults, greeting: 'नमस्ते {bot}, {business} में स्वागत है।' },
}, overrides: {} };
test('edits and resets one customer language without changing the UI locale or another language', async () => {
  vi.mocked(messages.load).mockResolvedValue(catalog);
  vi.mocked(messages.save).mockResolvedValue(catalog);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(createElement(BotMessageSettings, { workspaceId: 'workspace-one' }));
  fireEvent.click(screen.getByText('Customer-facing default messages'));
  await screen.findByRole('combobox', { name: 'Message language' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Message language' }), { target: { value: 'hi' } });
  const greeting = 'नमस्ते {bot}, {business} में आपका स्वागत है।';
  fireEvent.change(screen.getByLabelText('Greeting'), { target: { value: greeting } });
  fireEvent.click(screen.getByRole('button', { name: 'Save messages' }));
  await waitFor(() => expect(messages.save).toHaveBeenCalledWith('workspace-one', 'hi', { greeting }));
  await screen.findByText('Messages saved.');
  fireEvent.click(screen.getByRole('button', { name: 'Restore defaults' }));
  await waitFor(() => expect(messages.save).toHaveBeenLastCalledWith('workspace-one', 'hi', {}));
});
