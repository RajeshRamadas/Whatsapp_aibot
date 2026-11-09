import type { ErrorEvent, EventHint } from '@sentry/nextjs';

export function scrubEvent(event: ErrorEvent, hint: EventHint): ErrorEvent {
  hint.attachments = [];
  return {
    type: undefined,
    event_id: /^[a-f0-9]{32}$/.test(event.event_id ?? '') ? event.event_id : undefined,
    level: 'error',
    exception: { values: event.exception?.values?.map(value => ({
      type: 'ApplicationError', value: 'Error details removed for privacy',
      stacktrace: { frames: value.stacktrace?.frames?.flatMap(frame => {
        const file = frame.filename?.split(/[?#]/)[0].split('/').pop() ?? '';
        return /^(page|layout|server|global-error|webpack|main-app)\.js$/.test(file)
          ? [{ filename: file, lineno: typeof frame.lineno === 'number' ? frame.lineno : undefined }] : [];
      }) ?? [] },
    })) ?? [] },
  };
}

export const errorOptions = {
  defaultIntegrations: false as const, sendDefaultPii: false, tracesSampleRate: 0,
  enableLogs: false, enableMetrics: false, maxBreadcrumbs: 0, beforeSend: scrubEvent,
};
