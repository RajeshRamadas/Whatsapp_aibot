import { createElement } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Developer } from './developer';
import { developerClient } from '@/api/developer';
vi.mock('@/api/developer', () => ({
  scopes: ['knowledge:read', 'contacts:read', 'contacts:write'],
  developerClient: { list: vi.fn(), create: vi.fn(), revoke: vi.fn() },
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
describe('developer keys', () => {
  it('submits selected granular permissions and hides a secret on dismissal and workspace remount', async () => {
    vi.mocked(developerClient.list).mockResolvedValue([]);
    vi.mocked(developerClient.create).mockResolvedValue({ id: 'key', name: 'CRM', key: 'sk_live_once', key_prefix: 'sk_live', scopes: ['contacts:read'], last_used_at: null, revoked_at: null, expires_at: null });
    const view = render(createElement(Developer, { workspaceId: 'alpha', key: 'alpha' }));
    await screen.findByText('No API keys yet. Create one to connect an integration.');
    fireEvent.change(screen.getByLabelText('Key name'), { target: { value: 'CRM' } });
    fireEvent.click(screen.getByLabelText('Read knowledge')); fireEvent.click(screen.getByLabelText('Read contacts'));
    fireEvent.click(screen.getByRole('button', { name: 'Create API key' }));
    await screen.findByDisplayValue('sk_live_once');
    expect(developerClient.create).toHaveBeenCalledWith('CRM', ['contacts:read'], 90);
    fireEvent.click(screen.getByRole('button', { name: 'I saved the key' }));
    expect(screen.queryByDisplayValue('sk_live_once')).toBeNull();
    view.rerender(createElement(Developer, { workspaceId: 'beta', key: 'beta' }));
    await waitFor(() => expect(developerClient.list).toHaveBeenCalledTimes(3));
    expect(screen.queryByDisplayValue('sk_live_once')).toBeNull();
  });
  it('shows a retryable loading error and suppresses key creation without a permission', async () => {
    vi.mocked(developerClient.list).mockRejectedValue(new Error('offline'));
    render(createElement(Developer, { workspaceId: 'alpha' }));
    await screen.findByRole('alert');
    fireEvent.change(screen.getByLabelText('Key name'), { target: { value: 'CRM' } });
    fireEvent.click(screen.getByLabelText('Read knowledge'));
    expect((screen.getByRole('button', { name: 'Create API key' }) as HTMLButtonElement).disabled).toBe(true);
    vi.mocked(developerClient.list).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh keys' }));
    await screen.findByText('No API keys yet. Create one to connect an integration.');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
