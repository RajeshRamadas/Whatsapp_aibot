import { describe, expect, it } from 'vitest';
import { visiblePages } from './navigation';
describe('role-filtered navigation', () => {
  it('hides billing and settings from viewers', () => {
    const paths = visiblePages('viewer').map(page => page.path);
    expect(paths).toContain('/dashboard/inbox');
    expect(paths).not.toContain('/dashboard/billing');
    expect(paths).not.toContain('/dashboard/bot');
    expect(paths).not.toContain('/dashboard/campaigns');
  });
  it('shows billing to owners', () => expect(visiblePages('owner').some(page => page.key === 'billing')).toBe(true));
  it('limits campaigns to owners and admins', () => {
    expect(visiblePages('admin').some(page => page.key === 'campaigns')).toBe(true);
    expect(visiblePages('agent').some(page => page.key === 'campaigns')).toBe(false);
  });
});
