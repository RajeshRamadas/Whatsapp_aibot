import { describe, expect, it } from 'vitest';
import { usageProjection } from './usage-projection';

describe('usageProjection', () => {
  it('projects a quota date from the elapsed part of the UTC month', () => {
    expect(usageProjection('2026-11-01', '2026-11-11T00:00:00Z', 500, 1000)?.toISOString())
      .toBe('2026-11-21T00:00:00.000Z');
  });

  it('does not forecast after reset or after the quota is reached', () => {
    expect(usageProjection('2026-11-01', '2026-11-11T00:00:00Z', 100, 1000)).toBeNull();
    expect(usageProjection('2026-11-01', '2026-11-11T00:00:00Z', 1000, 1000)).toBeNull();
    expect(usageProjection('2026-11-01', '2026-12-01T00:00:00Z', 900, 1000)).toBeNull();
  });
});
