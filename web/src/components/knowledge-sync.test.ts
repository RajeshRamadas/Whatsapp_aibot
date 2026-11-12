import { createElement } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { KnowledgeSync } from './knowledge-sync';
import { knowledgeSyncClient as client } from '@/api/knowledge-sync';
vi.mock('@/api/knowledge-sync', () => ({ knowledgeSyncClient: { catalog: vi.fn(), notion: vi.fn(), drive: vi.fn(), change: vi.fn(), disconnect: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('recovers load errors and disables unconfigured Drive authorization', async () => {
  vi.mocked(client.catalog).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ drive_configured: false, connections: [], sources: [] });
  render(createElement(KnowledgeSync, { workspaceId: 'workspace' }));
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Refresh connections' }));
  await screen.findByText('No cloud knowledge connected yet.');
  fireEvent.change(screen.getByLabelText('Knowledge provider'), { target: { value: 'drive' } });
  await screen.findByText('Ask your platform administrator to configure Google Drive authorization.');
  expect((screen.getByRole('button', { name: 'Authorize Google Drive' }) as HTMLButtonElement).disabled).toBe(true);
  expect(client.drive).not.toHaveBeenCalled();
});
it('requires explicit read-only confirmation and never prepopulates secrets', async () => {
  vi.mocked(client.catalog).mockResolvedValue({ drive_configured: true, connections: [], sources: [] });
  render(createElement(KnowledgeSync, { workspaceId: 'workspace' }));
  const token = await screen.findByLabelText('Notion connection token') as HTMLInputElement;
  expect(token.value).toBe('');
  expect((screen.getByRole('button', { name: 'Connect Notion' }) as HTMLButtonElement).disabled).toBe(true);
});
