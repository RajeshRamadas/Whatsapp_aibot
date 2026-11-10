import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import * as websites from '@/api/websites';
import { WebsiteSources } from './website-sources';
vi.mock('@/api/websites', () => ({ list: vi.fn(), create: vi.fn(), refresh: vi.fn(), schedule: vi.fn(),
  remove: vi.fn(), pages: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const empty = { items: [], total: 0, limit: 20, offset: 0, page_limit: 50, reserved_pages: 0 };
test('single-page and same-host forms use the selected workspace and bounded crawl settings', async () => {
  vi.mocked(websites.list).mockResolvedValue(empty);
  render(createElement(WebsiteSources, { workspaceId: 'workspace-one' }));
  expect(screen.getByRole('status').textContent).toContain('Loading websites');
  await screen.findByText('No websites yet. Add a public page to teach your assistant.');
  fireEvent.change(screen.getByLabelText('Starting URL'), { target: { value: 'https://example.com/policy' } });
  fireEvent.click(screen.getByText('Follow links on the same host'));
  fireEvent.change(screen.getByLabelText('Maximum pages'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Refresh schedule'), { target: { value: 'weekly' } });
  fireEvent.click(screen.getByText('Add website'));
  await waitFor(() => expect(websites.create).toHaveBeenCalledWith('workspace-one', {
    url: 'https://example.com/policy', max_pages: 3, depth: 2, schedule: 'weekly',
  }));
});
test('load errors can recover and exhausted budgets offer an upgrade', async () => {
  vi.mocked(websites.list).mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ ...empty, page_limit: 0 });
  render(createElement(WebsiteSources, { workspaceId: 'workspace-two' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByText('Retry'));
  await screen.findByText('Upgrade your plan for more website pages');
  expect((screen.getByText('Add website') as HTMLButtonElement).disabled).toBe(true);
  expect(websites.list).toHaveBeenLastCalledWith('workspace-two', 0);
});
