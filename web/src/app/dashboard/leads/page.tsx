'use client';

import { useWorkspace } from '@/components/app-shell';
import { Leads } from '@/components/leads';

export default function LeadsPage() {
  const workspace = useWorkspace();
  return workspace ? <Leads key={workspace.id} workspace={workspace} /> : null;
}
