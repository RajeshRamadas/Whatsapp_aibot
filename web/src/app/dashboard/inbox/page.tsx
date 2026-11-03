'use client';

import { useWorkspace } from '@/components/app-shell';
import { Inbox } from '@/components/inbox';

export default function InboxPage() {
  const workspace = useWorkspace();
  return workspace ? <Inbox key={workspace.id} canAct={workspace.role !== 'viewer'} /> : null;
}
