import type { Metadata } from 'next';
import { InboxWorker } from '@/components/inbox-worker';

export const metadata: Metadata = { manifest: '/inbox.webmanifest' };

export default function InboxLayout({ children }: { children: React.ReactNode }) {
  return <><InboxWorker />{children}</>;
}
