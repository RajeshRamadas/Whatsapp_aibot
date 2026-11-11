import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Payments } from './payments';
import { paymentClient, paise } from '@/api/payments';
vi.mock('@/api/payments', async importOriginal => ({ ...await importOriginal<typeof import('@/api/payments')>(), paymentClient: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
test('INR entry preserves paise exactly and rejects invalid or unsupported amounts', () => {
  expect(paise('1250.50')).toBe(125050); expect(paise('1.01')).toBe(101); expect(paise('1000000')).toBe(100000000);
  for (const value of ['0.99', '1.001', '1e3', '-1', '1000000.01', 'NaN']) expect(paise(value)).toBe(null);
});
test('payment loading errors recover and agents receive no credential controls', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ catalog: { timezone: 'UTC', connections: [] },
    merchants: [], requests: [], conversations: [], bookings: [], templates: [] });
  vi.mocked(paymentClient).mockReturnValue({ load } as unknown as ReturnType<typeof paymentClient>);
  render(createElement(Payments, { workspaceId: 'workspace', admin: false }));
  expect(screen.getByRole('status').textContent).toContain('Loading payments');
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Retry payments' }));
  expect(await screen.findByText('No approved customer dues yet.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Save merchant connection' })).toBe(null);
});
