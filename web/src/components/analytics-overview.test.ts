import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { overview, type Overview } from '@/api/analytics';
import { AnalyticsOverview } from './analytics-overview';

vi.mock('@/api/analytics', () => ({ overview: vi.fn() }));
const load = vi.mocked(overview);
const summary = { conversations: 0, messages: 0, bot_answered: 0, handed_off: 0, leads: 0,
  bot_share: 0, handoff_rate: 0, first_response_seconds: null };
const empty: Overview = { from_date: '2026-10-01', to_date: '2026-10-02', updated_at: null,
  summary, daily: [], milestones: {} };
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe('analytics screen', () => {
  it('shows loading, then an empty report', async () => {
    let resolve!: (data: Overview) => void;
    load.mockReturnValue(new Promise(done => { resolve = done; }));
    render(createElement(AnalyticsOverview, { workspaceId: 'workspace-a' }));
    expect(screen.getByText('Loading analytics…')).toBeTruthy();
    resolve(empty);
    expect(await screen.findByText('No customer activity in this date range.')).toBeTruthy();
    expect(screen.getByText('No replies yet')).toBeTruthy();
  });

  it('recovers from an API error and applies the channel filter with workspace context', async () => {
    load.mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValue(empty);
    render(createElement(AnalyticsOverview, { workspaceId: 'workspace-a' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('No customer activity in this date range.');
    fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'whatsapp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() => expect(load).toHaveBeenLastCalledWith(
      expect.objectContaining({ channel: 'whatsapp' }), 'workspace-a', expect.any(AbortSignal)));
  });

  it('renders an accessible daily breakdown and milestone counts', async () => {
    const metrics = { ...summary, conversations: 2, messages: 4, bot_answered: 1, handed_off: 1,
      leads: 1, bot_share: 50, handoff_rate: 50, first_response_seconds: 20 };
    load.mockResolvedValue({ ...empty, summary: metrics,
      daily: [{ ...metrics, day: '2026-10-01', channel: 'web' }], milestones: { first_lead: 1 } });
    render(createElement(AnalyticsOverview, { workspaceId: 'workspace-a' }));
    expect(await screen.findByRole('table', { name: 'Daily activity' })).toBeTruthy();
    expect(screen.getAllByText('20 seconds')).toHaveLength(2);
    expect(screen.getByText('First lead captured')).toBeTruthy();
  });
});
