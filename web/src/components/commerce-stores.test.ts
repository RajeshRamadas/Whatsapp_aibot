import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CommerceStores } from './commerce-stores';
import { commerceClient, type StoreView } from '@/api/commerce';
vi.mock('@/api/commerce', () => ({ commerceClient: { list: vi.fn(), connect: vi.fn(), test: vi.fn(), sync: vi.fn(), disconnect: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const row: StoreView = { id: 'one', provider: 'shopify', url: 'https://demo.myshopify.com/', status: 'needs_test', source_id: null, product_count: 0, last_success: null, last_error: null, next_run: null, lease: null };
it('requires a passing test before sync and makes no external calls automatically', async () => {
  vi.mocked(commerceClient.list).mockResolvedValue([row]);
  render(createElement(CommerceStores, { workspaceId: 'workspace' }));
  await screen.findByText('Connection test required');
  expect((screen.getByRole('button', { name: 'Sync products now' }) as HTMLButtonElement).disabled).toBe(true);
  expect(commerceClient.test).not.toHaveBeenCalled(); expect(commerceClient.sync).not.toHaveBeenCalled();
  vi.mocked(commerceClient.test).mockResolvedValue({ ...row, status: 'connected' });
  vi.mocked(commerceClient.list).mockResolvedValue([{ ...row, status: 'connected' }]);
  fireEvent.click(screen.getByRole('button', { name: 'Test store connection' }));
  await screen.findByText('Connected');
  await waitFor(() => expect((screen.getByRole('button', { name: 'Sync products now' }) as HTMLButtonElement).disabled).toBe(false));
});
it('recovers a failed load and never prepopulates credentials', async () => {
  vi.mocked(commerceClient.list).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
  render(createElement(CommerceStores, { workspaceId: 'workspace' }));
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Refresh stores' }));
  await screen.findByText('No store connected yet.');
  expect((screen.getByLabelText('Admin API access token') as HTMLInputElement).value).toBe('');
  expect((screen.getByRole('button', { name: 'Save store' }) as HTMLButtonElement).disabled).toBe(true);
});
