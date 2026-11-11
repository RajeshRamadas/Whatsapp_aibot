import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { CalendarSettings } from './calendar-settings';
import { calendarClient } from '@/api/calendar';
vi.mock('@/api/calendar', () => ({ calendarClient: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
test('missing OAuth configuration disables authorization and explains the remaining setup', async () => {
  vi.mocked(calendarClient).mockReturnValue({ load: vi.fn().mockResolvedValue({ accounts: { configured: false, connections: [] }, bindings: [] }) } as unknown as ReturnType<typeof calendarClient>);
  render(createElement(CalendarSettings, { workspaceId: 'workspace', catalog: { timezone: 'UTC', services: [], staff: [] } }));
  fireEvent.click(screen.getByText('Google Calendar', { exact: true }));
  expect(await screen.findByText(/Google connection is not configured/)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Connect Google account' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('No calendar links. Manual availability is active.')).toBeTruthy();
});
test('calendar loading failures recover to the empty state', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ accounts: { configured: false, connections: [] }, bindings: [] });
  vi.mocked(calendarClient).mockReturnValue({ load } as unknown as ReturnType<typeof calendarClient>);
  render(createElement(CalendarSettings, { workspaceId: 'workspace', catalog: { timezone: 'UTC', services: [], staff: [] } }));
  fireEvent.click(screen.getByText('Google Calendar', { exact: true }));
  expect(screen.getByRole('status').textContent).toContain('Loading calendars');
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry calendars' }));
  expect(await screen.findByText('No Google accounts connected.')).toBeTruthy();
});
