import { describe, expect, it } from 'vitest';
import type { ErrorEvent, EventHint } from '@sentry/nextjs';
import { errorOptions, scrubEvent } from './error-privacy';

describe('error-report privacy', () => {
  it('drops browser/server payloads, identities, URLs, locals and attachments', () => {
    const privateText = 'private message 15551234567 secret-api-token';
    const event: ErrorEvent = { type: undefined, event_id: 'a'.repeat(32), message: privateText,
      user: { email: privateText }, request: { url: 'https://example.com/?token=' + privateText, data: privateText },
      extra: { password: privateText }, breadcrumbs: [{ message: privateText }],
      exception: { values: [{ value: privateText, stacktrace: { frames: [
        { filename: 'https://example.com/private/path/page.js?secret=' + privateText, vars: { body: privateText } },
      ] } }] } };
    const hint: EventHint = { attachments: [{ filename: 'private.txt', data: privateText }] };
    const report = scrubEvent(event, hint);
    expect(JSON.stringify(report)).not.toContain(privateText);
    expect(report).not.toHaveProperty('request');
    expect(report).not.toHaveProperty('breadcrumbs');
    expect(hint.attachments).toEqual([]);
    expect(report.exception?.values?.[0].stacktrace?.frames?.[0].filename).toBe('page.js');
    expect(errorOptions.enableLogs).toBe(false);
    expect(errorOptions.enableMetrics).toBe(false);
  });
});
