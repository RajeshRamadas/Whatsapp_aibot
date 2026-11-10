import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { KnowledgeManager } from './knowledge-manager';
import { api } from '@/lib/api';
vi.mock('@/lib/api', () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('shows loading and a recoverable error before the empty knowledge state', async () => {
  let reject!: (error: Error) => void;
  vi.mocked(api).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; })).mockResolvedValue([]);
  render(createElement(KnowledgeManager, { workspaceId: 'workspace', canEdit: true }));
  expect(screen.getByRole('status').textContent).toContain('Loading uploaded files');
  expect(screen.queryByText('No files yet. Upload a document to get started.')).toBeNull();
  reject(new Error('Knowledge service unavailable'));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('No files yet. Upload a document to get started.')).toBeTruthy();
  expect(screen.getByLabelText('Choose files').getAttribute('accept')).toContain('.png');
});

it('shows a recognition failure with useful recovery advice', async () => {
  vi.mocked(api).mockResolvedValue([{ id: 'one', filename: 'scan.pdf', status: 'failed',
    error: 'Text recognition took too long.' }]);
  render(createElement(KnowledgeManager, { workspaceId: 'workspace', canEdit: true }));
  expect((await screen.findByRole('alert')).textContent).toContain('Try a clearer scan or a smaller document.');
});
