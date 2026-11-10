export const pages = [
  { key: 'dashboard', path: '/dashboard', roles: ['owner', 'admin', 'agent', 'viewer'] },
  { key: 'inbox', path: '/dashboard/inbox', roles: ['owner', 'admin', 'agent', 'viewer'] },
  { key: 'knowledge', path: '/dashboard/knowledge', roles: ['owner', 'admin', 'agent', 'viewer'] },
  { key: 'botSettings', path: '/dashboard/bot', roles: ['owner', 'admin'] },
  { key: 'leads', path: '/dashboard/leads', roles: ['owner', 'admin', 'agent', 'viewer'] },
  { key: 'analytics', path: '/dashboard/analytics', roles: ['owner', 'admin', 'agent', 'viewer'] },
  { key: 'campaigns', path: '/dashboard/campaigns', roles: ['owner', 'admin'] },
  { key: 'appointments', path: '/dashboard/appointments', roles: ['owner', 'admin', 'agent'] },
  { key: 'team', path: '/dashboard/team', roles: ['owner', 'admin'] },
  { key: 'billing', path: '/dashboard/billing', roles: ['owner'] },
] as const;
export type Role = 'owner' | 'admin' | 'agent' | 'viewer';
export function visiblePages(role: Role) { return pages.filter(page => (page.roles as readonly string[]).includes(role)); }
