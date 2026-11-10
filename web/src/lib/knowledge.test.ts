import { describe, expect, it } from 'vitest';
import { failureAdvice, isPending, statusTone } from './knowledge';
describe('knowledge status helpers', () => {
  it('polls only active ingestion states', () => {
    expect(isPending('queued')).toBe(true);
    expect(isPending('processing')).toBe(true);
    expect(isPending('ready')).toBe(false);
    expect(isPending('failed')).toBe(false);
  });
  it('distinguishes ready and failed statuses', () => {
    expect(statusTone('ready')).toBe('success');
    expect(statusTone('failed')).toBe('danger');
  });
  it('suggests relevant fixes', () => {
    expect(failureAdvice('No text extracted')).toBe('emptyFileAdvice');
    expect(failureAdvice('Unsupported format')).toBe('formatAdvice');
    expect(failureAdvice('Text recognition took too long.')).toBe('ocrAdvice');
  });
});
