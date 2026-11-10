import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import * as reviews from '@/api/improvements';
import { ImproveAnswers } from './improve-answers';
vi.mock('@/api/improvements', () => ({ list: vi.fn(), correct: vi.fn(), resolve: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const row: reviews.Review = { id: 'review-one', question: 'Refund period?', answer: 'Unknown', unanswered: true,
  feedback: 'down', state: 'open', created_at: '2026-11-10T10:00:00Z', conversation_id: 'conversation-one',
  channel: 'web', sources: [], draft_question: null, draft_answer: null, qa_pair_id: null, error: null };
const data: reviews.ReviewList = { items: [row], total: 1, limit: 20, offset: 0, summary: {
  questions: 1, unanswered: 1, positive: 0, negative: 1, satisfaction: 0, top_questions: [],
} };
test('a flagged reply opens a human-authored correction and sends it in the selected workspace', async () => {
  vi.mocked(reviews.list).mockResolvedValue(data);
  render(createElement(ImproveAnswers, { workspaceId: 'workspace-one' }));
  expect(screen.getByRole('status').textContent).toContain('Loading answer reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Add a Q&A answer' }));
  expect((screen.getByLabelText('Q&A question') as HTMLTextAreaElement).value).toBe('Refund period?');
  expect((screen.getByLabelText('Approved answer') as HTMLTextAreaElement).value).toBe('');
  fireEvent.change(screen.getByLabelText('Approved answer'), { target: { value: 'Within 14 days.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Q&A' }));
  await waitFor(() => expect(reviews.correct).toHaveBeenCalledWith('workspace-one', 'review-one', {
    question: 'Refund period?', answer: 'Within 14 days.',
  }));
});
test('load errors recover to an empty state with no satisfaction score', async () => {
  vi.mocked(reviews.list).mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ ...data, items: [],
    total: 0, summary: { ...data.summary, questions: 0, negative: 0, unanswered: 0, satisfaction: null } });
  render(createElement(ImproveAnswers, { workspaceId: 'workspace-two' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('No replies match these filters.');
  expect(screen.getByText('—')).toBeTruthy();
});
