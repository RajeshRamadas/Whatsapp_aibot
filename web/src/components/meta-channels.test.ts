import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MetaChannels } from './meta-channels';
import { metaClient } from '@/api/meta-dm';
import { ApiRequestError } from '@/lib/api';
vi.mock('./meta-authorization', () => ({ MetaAuthorization: () => null }));
vi.mock('@/api/meta-dm', () => ({ metaClient: { list: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), setBot: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); });
const row = { id: 'channel', kind: 'instagram', external_id: '12345', display_name: 'Shop account', status: 'configured' };
it('verifies on submission, clears the token and disconnects explicitly', async () => {
  vi.mocked(metaClient.list).mockResolvedValueOnce([]).mockResolvedValueOnce([row]).mockResolvedValueOnce([]);
  vi.mocked(metaClient.connect).mockResolvedValue(row); vi.mocked(metaClient.disconnect).mockResolvedValue();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  await screen.findByText('No Instagram or Messenger accounts connected.');
  expect(metaClient.connect).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Meta account or Page ID'), { target: { value: '12345' } });
  fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'private-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and connect' }));
  await screen.findByText('Shop account');
  expect(metaClient.connect).toHaveBeenCalledWith({ kind: 'instagram', external_id: '12345', access_token: 'private-token' });
  expect((screen.getByLabelText('Access token') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect account' }));
  await screen.findByText('No Instagram or Messenger accounts connected.');
  expect(metaClient.disconnect).toHaveBeenCalledWith('channel');
});
it.each([
  [409, 'This account or channel type is already connected.'],
  [422, 'Check the selected channel, account ID and token'],
  [424, 'Meta subscription cleanup could not be confirmed.'],
  [502, 'Meta could not verify this account'],
  [503, 'Messaging setup is unavailable.'],
])('explains HTTP %s without displaying private server details', async (status, message) => {
  vi.mocked(metaClient.list).mockResolvedValue([]);
  vi.mocked(metaClient.connect).mockRejectedValue(new ApiRequestError(Number(status), 'safe', 'private-token'));
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  await screen.findByText('No Instagram or Messenger accounts connected.');
  fireEvent.change(screen.getByLabelText('Meta account or Page ID'), { target: { value: '12345' } });
  fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'private-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and connect' }));
  expect((await screen.findByRole('alert')).textContent).toContain(String(message));
  expect(document.body.textContent).not.toContain('private-token');
});
it('explains why an in-flight reply prevents disconnecting', async () => {
  vi.mocked(metaClient.list).mockResolvedValue([row]);
  vi.mocked(metaClient.disconnect).mockRejectedValue(new ApiRequestError(409, 'conflict', 'private detail'));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Disconnect account' }));
  expect((await screen.findByRole('alert')).textContent).toContain('A reply is being sent.');
});
it('recovers load failures and clears credentials after failed verification', async () => {
  vi.mocked(metaClient.list).mockRejectedValueOnce(new Error('private response')).mockResolvedValue([]);
  vi.mocked(metaClient.connect).mockRejectedValue(new Error('private-token rejected'));
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Refresh channels' }));
  await screen.findByText('No Instagram or Messenger accounts connected.');
  fireEvent.change(screen.getByLabelText('Meta account or Page ID'), { target: { value: '12345' } });
  fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'private-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and connect' }));
  await screen.findByRole('alert');
  await waitFor(() => expect((screen.getByLabelText('Access token') as HTMLInputElement).value).toBe(''));
  expect(document.body.textContent).not.toContain('private-token');
});
it('clears private fields when the workspace changes', async () => {
  vi.mocked(metaClient.list).mockResolvedValue([]);
  const view = render(createElement(MetaChannels, { workspaceId: 'one' }));
  await screen.findByText('No Instagram or Messenger accounts connected.');
  fireEvent.change(screen.getByLabelText('Meta account or Page ID'), { target: { value: '12345' } });
  fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'private-token' } });
  view.rerender(createElement(MetaChannels, { workspaceId: 'two' }));
  await waitFor(() => expect((screen.getByLabelText('Access token') as HTMLInputElement).value).toBe(''));
  expect((screen.getByLabelText('Meta account or Page ID') as HTMLInputElement).value).toBe('');
  expect(metaClient.connect).not.toHaveBeenCalled();
});

it('enables bot replies explicitly and refreshes the confirmed account mode', async () => {
  vi.mocked(metaClient.list).mockResolvedValueOnce([row]).mockResolvedValueOnce([{ ...row, meta_bot_enabled: true }]);
  vi.mocked(metaClient.setBot).mockResolvedValue({ channel_id: row.id, enabled: true });
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Enable bot replies' }));
  await screen.findByText('Bot replies enabled for this account');
  expect(metaClient.setBot).toHaveBeenCalledWith(row.id, true);
  expect(screen.getByRole('button', { name: 'Disable bot replies' })).toBeTruthy();
});
it('explains an in-flight bot send without changing the displayed mode', async () => {
  vi.mocked(metaClient.list).mockResolvedValue([{ ...row, meta_bot_enabled: true }]);
  vi.mocked(metaClient.setBot).mockRejectedValue(new ApiRequestError(409, 'conflict', 'private-provider-body'));
  render(createElement(MetaChannels, { workspaceId: 'one' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Disable bot replies' }));
  expect((await screen.findByRole('alert')).textContent).toContain('A bot reply is being sent.');
  expect(document.body.textContent).not.toContain('private-provider-body');
  expect(screen.getByText('Bot replies enabled for this account')).toBeTruthy();
});
