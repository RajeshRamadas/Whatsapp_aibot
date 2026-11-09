'use client';
import { useEffect } from 'react';
import { captureException } from '@sentry/nextjs';
import copy from '@/messages/errors.en.json';

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => { captureException(error); }, [error]);
  return <html lang="en"><head><title>{copy.title}</title></head><body>
    <main style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
    <h1>{copy.title}</h1><p>{copy.description}</p>
    <button style={{ padding: '.75rem 1rem' }} onClick={reset}>{copy.retry}</button>
  </main></body></html>;
}
