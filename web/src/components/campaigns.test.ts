import { createElement } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Campaigns } from './campaigns';
import CampaignPage from '@/app/dashboard/campaigns/page';
import { client, type Campaign } from '@/api/campaigns';
import { useWorkspace } from './app-shell';

vi.mock('@/api/campaigns', () => ({ client: vi.fn() }));
vi.mock('./app-shell', () => ({ useWorkspace: vi.fn() }));
const get = vi.fn();
beforeEach(() => {
  vi.mocked(client).mockReturnValue({ get, workspaceId: 'workspace-a' } as unknown as ReturnType<typeof client>);
  vi.stubGlobal('EventSource', class { onopen = null; onerror = null; addEventListener() {} close() {} });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it('shows loading and a recoverable error, then the empty state', async () => {
  let reject!: (error: Error) => void;
  get.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; })).mockResolvedValue([]);
  render(createElement(Campaigns, { workspaceId: 'workspace-a' }));
  expect(screen.getByText('Loading…')).toBeTruthy();
  reject(new Error('Unavailable'));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('No records yet.')).toBeTruthy();
  expect(client).toHaveBeenCalledWith('workspace-a');
});

it('renders a filled campaign and recipient drill-down', async () => {
  const report: Campaign = { id: 'one', name: 'Campaign example', state: 'completed', reason: null,
    scheduled_at: null, recipients: 1, excluded: 2, currency: 'INR', estimated_max_cost: '0.25',
    estimated_billable_cost: '0.25', pricing_unconfirmed: 0, test_accepted: true, test_state: 'read',
    results: { sent: 1, delivered: 1, read: 1, replied: 0, opt_outs: 0 },
    preview: [{ contact_id: 'contact', text: 'Hello Sample' }] };
  get.mockImplementation((path: string) => Promise.resolve(path.includes('recipients') ? [] : path.includes('?') ? [report] : report));
  render(createElement(Campaigns, { workspaceId: 'workspace-a' }));
  fireEvent.click(await screen.findByRole('button', { name: 'View campaign: Campaign example' }));
  expect(await screen.findByText('Hello Sample')).toBeTruthy();
  expect(screen.getByRole('table', { name: 'Recipient results' })).toBeTruthy();
});

it('blocks a viewer before mounting any campaign data requests', () => {
  vi.mocked(useWorkspace).mockReturnValue({ id: 'workspace-a', name: 'Demo', role: 'viewer' });
  render(createElement(CampaignPage));
  expect(screen.getByText('Only workspace owners and admins can manage campaigns.')).toBeTruthy();
  expect(client).not.toHaveBeenCalled();
});
