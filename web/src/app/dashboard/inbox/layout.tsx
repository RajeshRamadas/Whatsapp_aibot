import type { Metadata } from 'next';
import { InboxNotificationSettings } from '@/components/inbox-notification-settings';

export const metadata: Metadata = { manifest: '/inbox.webmanifest' };

export default function InboxLayout({ children }: { children: React.ReactNode }) {
  return <><InboxNotificationSettings />{children}</>;
}
