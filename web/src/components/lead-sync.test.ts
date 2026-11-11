import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { LeadSync } from './lead-sync';
import { syncClient } from '@/api/lead-sync';
vi.mock('@/api/lead-sync', () => ({ syncClient: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
test('lead destination loading recovers and unavailable Google OAuth is explained', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ google_configured: false, sources: [], connections: [] });
  vi.mocked(syncClient).mockReturnValue({ load } as unknown as ReturnType<typeof syncClient>);
  render(createElement(LeadSync, { workspaceId: 'workspace' }));
  expect(screen.getByRole('status').textContent).toContain('Loading lead connections');
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Retry loading' }));
  expect(await screen.findByText('No lead destinations connected yet.')).toBeTruthy();
  fireEvent.click(screen.getByText('Add a lead destination', { exact: true }));
  expect(screen.getByRole('button', { name: 'Connect Google Sheets' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByText('Google Sheets setup is pending. Ask your administrator to configure the Google OAuth app.')).toBeTruthy();
});
