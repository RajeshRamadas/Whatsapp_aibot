import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SpreadsheetTables } from './spreadsheet-tables';
import { tableClient, type SheetCatalog } from '@/api/spreadsheets';
vi.mock('@/api/spreadsheets', () => ({ tableClient: { catalog: vi.fn(), enable: vi.fn(), preview: vi.fn(), query: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const data: SheetCatalog = { sources: [{ id: 'source', filename: 'plots.csv', status: 'ready', error: null }], tables: [{ id: 'one', source_id: 'source', title: 'CSV', table_name: 's_demo', row_count: 4, enabled: false, columns: [{ name: 'c1', label: 'Price', type: 'numeric', currency: '₹' }] }] };
it('previews exact values and enables only through an explicit user action', async () => {
  vi.mocked(tableClient.catalog).mockResolvedValue(data);
  vi.mocked(tableClient.preview).mockResolvedValue({ columns: ['c1'], rows: [['3500000.10']], truncated: false, untrusted: true, source: { id: 'source', filename: 'plots.csv', heading: 'CSV' } });
  vi.mocked(tableClient.enable).mockResolvedValue({ ...data.tables[0], enabled: true });
  render(createElement(SpreadsheetTables, { workspaceId: 'workspace' }));
  await screen.findByText('Exact answers paused', { exact: false }); expect(tableClient.enable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview rows' }));
  await screen.findByRole('cell', { name: '3500000.10' });
  fireEvent.click(screen.getByRole('button', { name: 'Enable exact answers' }));
  await waitFor(() => expect(tableClient.enable).toHaveBeenCalledWith('one', true));
});
it('shows limited results and clears them from the private view', async () => {
  vi.mocked(tableClient.catalog).mockResolvedValue(data);
  vi.mocked(tableClient.preview).mockResolvedValue({ columns: ['c1'], rows: [['99']], truncated: true, untrusted: true, source: { id: 'source', filename: 'plots.csv', heading: 'CSV' } });
  render(createElement(SpreadsheetTables, { workspaceId: 'workspace' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Preview rows' }));
  await screen.findByText(/The displayed rows are limited/);
  fireEvent.click(screen.getByRole('button', { name: 'Clear results' }));
  expect(screen.queryByRole('cell', { name: '99' })).toBeNull();
});
it('recovers a failed catalog load through refresh', async () => {
  vi.mocked(tableClient.catalog).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ sources: [], tables: [] });
  render(createElement(SpreadsheetTables, { workspaceId: 'workspace' })); await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh spreadsheets' }));
  await screen.findByText('No spreadsheets yet. Upload a CSV or XLSX in Documents.');
  expect(screen.queryByRole('alert')).toBeNull();
});
