import { describe, expect, it } from 'vitest';
import { motion, spacing } from './tokens';
describe('design tokens', () => { it('uses a 4px spacing grid', () => { expect(Object.values(spacing).every(n => n % 4 === 0)).toBe(true); }); it('keeps motion brief', () => { expect(motion.fast).toBeGreaterThanOrEqual(150); expect(motion.slow).toBeLessThanOrEqual(250); }); });
