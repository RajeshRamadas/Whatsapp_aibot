import * as Sentry from '@sentry/nextjs';
import { errorOptions } from './src/lib/error-privacy';

export function register() {
  if (process.env.SENTRY_DSN) Sentry.init({ ...errorOptions, dsn: process.env.SENTRY_DSN });
}
export const onRequestError = Sentry.captureRequestError;
