import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Accuracy } from './accuracy';
import { accuracyClient } from '@/api/accuracy';
vi.mock('@/api/accuracy', () => ({ accuracyClient: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
test('accuracy loading errors recover and viewers receive no paid-run or editing controls', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ cases: [], runs: [], max_run_cases: 100 });
  vi.mocked(accuracyClient).mockReturnValue({ load } as unknown as ReturnType<typeof accuracyClient>);
  render(createElement(Accuracy, { workspaceId: 'workspace', admin: false }));
  expect(screen.getByRole('status').textContent).toContain('Loading accuracy runs');
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Retry accuracy' }));
  expect(await screen.findByText('No accuracy runs yet. Select questions and start a run to save a baseline.')).toBeTruthy();
  expect(screen.queryByRole('form', { name: 'Run selected questions' })).toBe(null);
  expect(screen.queryByText('Add or edit test questions')).toBe(null);
});
