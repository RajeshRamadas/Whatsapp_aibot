import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OutgoingWebhooks } from './outgoing-webhooks';
import { webhookClient, type Endpoint } from '@/api/outgoing-webhooks';
vi.mock('@/api/outgoing-webhooks', () => ({ eventTypes: ['lead.created'], webhookClient: { list: vi.fn(), enable: vi.fn(), create: vi.fn(), deliveries: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const endpoint: Endpoint = { id: 'one', name: 'Orders', url: 'https://example.com/hook', events: ['lead.created'], secret_last4: '1234', enabled: false, disabled_reason: null, failure_since: null, last_success_at: null, last_error: null, created_at: '2026-11-11T12:00:00Z' };
it('requires explicit sharing approval before enabling a destination', async () => {
  vi.mocked(webhookClient.list).mockResolvedValue([endpoint]);
  vi.mocked(webhookClient.deliveries).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(webhookClient.enable).mockResolvedValue({ ...endpoint, enabled: true });
  render(createElement(OutgoingWebhooks, { workspaceId: 'workspace' }));
  const button = await screen.findByRole('button', { name: 'Enable delivery' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('I approve sending these record IDs and status metadata to this destination.'));
  fireEvent.click(button);
  await waitFor(() => expect(webhookClient.enable).toHaveBeenCalledWith('one', true));
});
it('recovers an endpoint loading failure through refresh', async () => {
  vi.mocked(webhookClient.list).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
  render(createElement(OutgoingWebhooks, { workspaceId: 'workspace' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh destinations' }));
  await screen.findByText('No event destinations yet.'); expect(screen.queryByRole('alert')).toBeNull();
});
