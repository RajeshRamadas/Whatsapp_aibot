import { api } from '@/lib/api';
import type { components } from './schema';
type Schema = components['schemas'];
export type Catalog = Schema['Catalog'];
export type Service = Schema['ServiceView'];
export type Staff = Schema['StaffView'];
export type Slot = Schema['SlotView'];
export type Booking = Schema['BookingView'];
export type Language = Exclude<Schema['Hold']['language'], null | undefined>;
export type Conversation = { id: string; channel: string; user: string | null };
export type Template = Schema['TemplateView'];
export type Action = (operation: () => Promise<unknown>) => Promise<boolean>;
export const client = (workspaceId: string) => {
  const request = <T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) => api<T>(path, {
    method, headers: { 'X-Workspace-Id': workspaceId }, signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return {
    load: (query: URLSearchParams, signal: AbortSignal) => Promise.all([
      request<Catalog>('appointments/catalog', 'GET', undefined, signal),
      request<Slot[]>('appointments/slots?' + query, 'GET', undefined, signal),
      request<Booking[]>('appointments/bookings?' + query, 'GET', undefined, signal),
      request<Conversation[]>('conversations?limit=200', 'GET', undefined, signal),
      request<Template[]>('whatsapp/templates?approved=true', 'GET', undefined, signal),
    ]),
    service: (body: Schema['ServiceInput'], id?: string) => request('appointments/services' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', body),
    staff: (body: Schema['StaffInput'], id?: string) => request('appointments/staff' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', body),
    slot: (body: Schema['SlotInput']) => request('appointments/slots', 'POST', body),
    remove: (id: string) => request('appointments/slots/' + id, 'DELETE'),
    hold: (body: Schema['Hold']) => request<Booking>('appointments/bookings', 'POST', body),
    change: (id: string, action: 'confirm' | 'cancel' | 'retry-reminder') => request('appointments/bookings/' + id + '/' + action, 'POST', { confirm: true }),
  };
};
export type Client = ReturnType<typeof client>;
