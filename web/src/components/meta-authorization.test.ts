import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MetaAuthorization } from './meta-authorization';
import { metaOAuthClient as client, type MetaAuthorizationView } from '@/api/meta-oauth';
vi.mock('@/api/meta-oauth', () => ({ metaOAuthClient: { config: vi.fn(), start: vi.fn(), status: vi.fn(), select: vi.fn(), cancel: vi.fn() } }));
const id = '12345678-1234-1234-1234-123456789abc';
const selecting: MetaAuthorizationView = { id, kind: 'instagram', status: 'selecting', channel_id: null, error: null,
  accounts: [{ external_id: '123', name: 'Shop Instagram', kind: 'instagram' }] };
function show(onConnected = vi.fn(), prepare = () => {}) {
  window.history.replaceState(null, '', '/dashboard/channels?meta_oauth=' + id);
  vi.mocked(client.config).mockResolvedValue({ configured: true });
  vi.mocked(client.status).mockResolvedValue(selecting);
  prepare();
  return render(createElement(MetaAuthorization, { available: ['instagram'], onConnected }));
}
afterEach(() => { cleanup(); vi.resetAllMocks(); window.history.replaceState(null, '', '/'); });
it('disables authorization until platform setup is available', async () => {
  vi.mocked(client.config).mockResolvedValue({ configured: false });
  render(createElement(MetaAuthorization, { available: ['instagram'], onConnected: vi.fn() }));
  await screen.findByText('Ask your platform administrator to configure Meta authorization.');
  expect((screen.getByRole('button', { name: 'Authorize with Meta' }) as HTMLButtonElement).disabled).toBe(true);
});
it('starts renewal for the selected existing account', async () => {
  vi.mocked(client.config).mockResolvedValue({ configured: true });
  vi.mocked(client.start).mockRejectedValue(new Error('Simulated response failure'));
  const accounts = [{ id, kind: 'messenger', external_id: '123', display_name: 'Shop', status: 'configured' }];
  render(createElement(MetaAuthorization, { available: ['instagram'], accounts, onConnected: vi.fn() }));
  await screen.findByRole('option', { name: 'Messenger (Reauthorize with Meta)' });
  fireEvent.change(screen.getByRole('combobox'), { target: { value: id } });
  const reauthorize = screen.getByRole('button', { name: 'Reauthorize with Meta' }) as HTMLButtonElement;
  await waitFor(() => expect(reauthorize.disabled).toBe(false));
  fireEvent.click(reauthorize);
  await waitFor(() => expect(client.start).toHaveBeenCalledWith('messenger', expect.any(AbortSignal), id));
  await screen.findByRole('alert');
});
it('selects only a returned account and refreshes channels once after completion', async () => {
  const done = vi.fn(); show(done);
  await screen.findByRole('option', { name: 'Shop Instagram (123)' });
  expect((screen.getByRole('button', { name: 'Connect selected account' }) as HTMLButtonElement).disabled).toBe(true);
  const complete = { ...selecting, status: 'complete', accounts: [], channel_id: 'channel' };
  vi.mocked(client.select).mockResolvedValue(complete); vi.mocked(client.status).mockResolvedValue(complete);
  fireEvent.change(screen.getByLabelText('Authorized account'), { target: { value: '123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect selected account' }));
  await screen.findByText('Account connected. You can now reply from Inbox.');
  expect(client.select).toHaveBeenCalledWith(id, '123', expect.any(AbortSignal));
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1)); expect(window.location.search).toBe('');
});
it('cancels an attempt without connecting an account', async () => {
  show(); await screen.findByLabelText('Authorized account');
  const cancelled = { ...selecting, status: 'cancelled', accounts: [] };
  vi.mocked(client.cancel).mockResolvedValue(cancelled); vi.mocked(client.status).mockResolvedValue(cancelled);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel authorization' }));
  await screen.findByText('Authorization cancelled.'); expect(client.select).not.toHaveBeenCalled();
  expect(window.location.search).toBe('');
});
it('uses safe errors and recovers a failed status request', async () => {
  show(vi.fn(), () => { vi.mocked(client.status).mockRejectedValueOnce(new Error('private-provider-token')); });
  await screen.findByRole('alert'); expect(document.body.textContent).not.toContain('private-provider-token');
  fireEvent.click(screen.getByRole('button', { name: 'Retry authorization status' }));
  await screen.findByLabelText('Authorized account');
});
it('ignores a late status result after workspace unmount', async () => {
  const done = vi.fn();
  let resolve!: (value: MetaAuthorizationView) => void;
  const view = show(done, () => { vi.mocked(client.status).mockImplementationOnce(() => new Promise(accept => { resolve = accept; })); });
  await waitFor(() => expect(client.status).toHaveBeenCalled());
  view.unmount(); resolve({ ...selecting, status: 'complete' });
  await Promise.resolve(); expect(done).not.toHaveBeenCalled();
});
