import * as Sentry from '@sentry/nextjs';
import { errorOptions } from './src/lib/error-privacy';

if (process.env.NEXT_PUBLIC_SENTRY_DSN) Sentry.init({
  ...errorOptions, dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  integrations: [Sentry.globalHandlersIntegration(), Sentry.browserApiErrorsIntegration()],
});
