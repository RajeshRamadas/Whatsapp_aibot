import { createElement } from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AccountSecurity } from './account-security';
import { SecondFactor } from './second-factor';
import { securityClient } from '@/api/security';
vi.mock('@/api/security', () => ({ securityClient: { status: vi.fn(), verify: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
test('security status recovers and does not offer enrollment to non-admin accounts', async () => {
  vi.mocked(securityClient.status).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({
    enabled: false, eligible: false, configured: true, email_verified: true, recovery_remaining: 0, setup_pending: false });
  render(createElement(AccountSecurity));
  expect(screen.getByRole('status').textContent).toContain('Loading account security');
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Retry security status' }));
  expect(await screen.findByText('Two-factor authentication not enabled')).toBeTruthy();
  expect(screen.queryByRole('form', { name: 'Set up authenticator' })).toBe(null);
});
test('second-factor failure clears the proof and never calls the authenticated completion handler', async () => {
  vi.mocked(securityClient.verify).mockRejectedValue(new Error('invalid'));
  const complete = vi.fn(), restart = vi.fn();
  render(createElement(SecondFactor, { challenge: 'nonce', complete, restart }));
  const code = screen.getByLabelText('Authenticator code');
  fireEvent.change(code, { target: { value: '123456' } }); fireEvent.submit(screen.getByRole('form'));
  await screen.findByRole('alert'); expect((code as HTMLInputElement).value).toBe(''); expect(complete).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Use a recovery code' }));
  expect(screen.getByLabelText('Recovery code').getAttribute('type')).toBe('password');
  fireEvent.click(screen.getByRole('button', { name: 'Restart sign-in' })); expect(restart).toHaveBeenCalledOnce();
});
