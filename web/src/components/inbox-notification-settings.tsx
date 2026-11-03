'use client';

import { useWorkspace } from '@/components/app-shell';
import { InboxWorker } from './inbox-worker';

export function InboxNotificationSettings() {
  const workspace = useWorkspace();
  return workspace ? <InboxWorker key={workspace.id} /> : null;
}
