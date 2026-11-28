import { cleanup, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { listIndustryTemplates } from '@/api/workspaces';
import { IndustryPicker } from './workspace-industry';

vi.mock('@/api/workspaces', () => ({ listIndustryTemplates: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('keeps the general workspace option available when the catalog fails to load', async () => {
  let fail!: (cause: Error) => void;
  vi.mocked(listIndustryTemplates).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
  render(createElement(IndustryPicker, { value: '', onChange: vi.fn(), disabled: false }));

  const picker = screen.getByRole<HTMLSelectElement>('combobox', { name: 'Industry template' });
  expect(screen.getByRole('status')).toBeTruthy();
  fail(new Error('Catalog unavailable'));

  expect((await screen.findByRole('alert')).textContent).toContain('You can create a general workspace.');
  expect(picker.selectedOptions[0].textContent).toBe('Other');
  expect(picker.disabled).toBe(false);
});
