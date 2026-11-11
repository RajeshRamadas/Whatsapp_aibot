import { api } from '@/lib/api';
import type { components } from './schema';
type S = components['schemas'];
export type Endpoint = S['EndpointView'];
export type Delivery = S['DeliveryView'];
export type EventType = S['EndpointInput']['events'][number];
export const eventTypes: EventType[] = ['message.received', 'message.sent', 'conversation.handoff', 'conversation.closed', 'lead.created', 'lead.updated', 'booking.created', 'payment.completed', 'campaign.completed'];
export const webhookClient = {
  list: (signal?: AbortSignal) => api<Endpoint[]>('webhook-endpoints', { signal }),
  create: (name: string, url: string, events: EventType[]) => api<S['CreatedEndpoint']>('webhook-endpoints', { method: 'POST', body: JSON.stringify({ name, url, events }) }),
  enable: (id: string, enabled: boolean) => api<Endpoint>('webhook-endpoints/' + id, { method: 'PATCH', body: JSON.stringify({ enabled, ...(enabled ? { confirm_sharing: true } : {}) }) }),
  remove: (id: string) => api<void>('webhook-endpoints/' + id, { method: 'DELETE' }),
  deliveries: (id: string, cursor?: string, signal?: AbortSignal) => api<S['DeliveryPage']>('webhook-endpoints/' + id + '/deliveries' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''), { signal }),
  resend: (id: string, delivery: string, request_key: string) => api<Delivery>('webhook-endpoints/' + id + '/deliveries/' + delivery + '/resend', { method: 'POST', body: JSON.stringify({ request_key, confirm: true }) }),
};
