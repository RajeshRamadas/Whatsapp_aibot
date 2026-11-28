import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { leadFollowUps } from '@/api/lead-follow-ups';
import { LeadFollowUps } from './lead-follow-ups';

vi.mock('@/api/lead-follow-ups', () => ({ leadFollowUps: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('can pause a saved rule when the template catalog is unavailable', async () => {
  const active = { enabled: true, template_id: 'approved', delay_hours: 24, values: [], template_available: false };
  const save = vi.fn().mockResolvedValue({ ...active, enabled: false });
  vi.mocked(leadFollowUps).mockReturnValue({ read: vi.fn().mockResolvedValue(active),
    templates: vi.fn().mockRejectedValue(new Error('Unavailable')), save });
  render(createElement(LeadFollowUps, { workspaceId: 'salon' }));
  expect(await screen.findByText('Could not load approved templates.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Pause follow-ups' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ enabled: false }));
  expect(await screen.findByText('Follow-ups paused')).toBeTruthy();
});
