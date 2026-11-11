import { api } from '@/lib/api';
import type { components } from './schema';
import type { Booking, Conversation, Template } from './appointments';
type Schema = components['schemas'];
export type Merchant = Schema['MerchantView'];
export type Payment = Schema['PaymentView'];
export type PaymentData = { catalog: Schema['PaymentCatalog']; merchants: Merchant[]; requests: Payment[];
  conversations: Conversation[]; bookings: Booking[]; templates: Template[] };
export function paise(value: string): number | null {
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value)) return null;
  const [whole, part = ''] = value.split('.'), result = Number(whole) * 100 + Number(part.padEnd(2, '0'));
  return result >= 100 && result <= 100000000 ? result : null;
}
export const paymentClient = (workspaceId: string) => {
  const request = <T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) => api<T>(path, {
    method, headers: { 'X-Workspace-Id': workspaceId }, signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return {
    load: async (admin: boolean, signal: AbortSignal): Promise<PaymentData> => {
      const [catalog, requests, conversations, bookings, merchants, templates] = await Promise.all([
        request<Schema['PaymentCatalog']>('payments/catalog', 'GET', undefined, signal),
        request<Payment[]>('payments/requests', 'GET', undefined, signal),
        request<Conversation[]>('conversations?limit=200', 'GET', undefined, signal),
        request<Booking[]>('appointments/bookings', 'GET', undefined, signal),
        admin ? request<Merchant[]>('payments/connections', 'GET', undefined, signal) : Promise.resolve([]),
        admin ? request<Template[]>('whatsapp/templates?approved=true', 'GET', undefined, signal) : Promise.resolve([]),
      ]);
      return { catalog, requests, conversations, bookings, merchants, templates };
    },
    connect: (body: Schema['MerchantInput']) => request<Merchant>('payments/connections', 'POST', body),
    test: (id: string) => request('payments/connections/' + id + '/test', 'POST'),
    disconnect: (id: string) => request('payments/connections/' + id, 'DELETE', { confirm: true }),
    approve: (body: Schema['PaymentApproval']) => request('payments/requests', 'POST', body),
    change: (id: string, action: 'send' | 'sync' | 'cancel') => request('payments/requests/' + id + '/' + action, 'POST', { confirm: true }),
  };
};
export type PaymentClient = ReturnType<typeof paymentClient>;
