import type { components } from './schema';
import { api } from '@/lib/api';
export type Attachment = components['schemas']['AttachmentView'];
export function queueMedia(conversation: string, file: File, caption: string, key: string) {
  const body = new FormData(); body.set('file', file); body.set('caption', caption);
  return api<components['schemas']['QueuedMedia']>('inbox/conversations/' + encodeURIComponent(conversation)
    + '/media', { method: 'POST', body, headers: { 'Idempotency-Key': key } });
}
export const mediaUrl = (conversation: string, attachment: Attachment) => '/api/backend/conversations/'
  + encodeURIComponent(conversation) + '/media/' + encodeURIComponent(attachment.id);
