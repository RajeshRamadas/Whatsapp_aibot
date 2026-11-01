import { describe, expect, it } from 'vitest';
import { csrfToken } from './api';
describe('csrfToken', () => {
  it('reads double-submit token', () => expect(csrfToken('x=1; chatbot_csrf=a%2Bb; y=2')).toBe('a+b'));
  it('returns empty when missing', () => expect(csrfToken('x=1')).toBe(''));
});
