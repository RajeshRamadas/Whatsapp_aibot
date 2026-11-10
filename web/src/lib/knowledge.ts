export type Source = { id: string; filename: string; status: string; error: string | null };
export type UploadIntent = { source_id: string; upload_url: string; method: 'PUT'; content_type: string };
export function statusTone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'ready') return 'success';
  if (status === 'failed') return 'danger';
  if (['queued', 'processing', 'uploading', 'ingesting'].includes(status)) return 'warning';
  return 'neutral';
}
export function isPending(status: string): boolean {
  return ['queued', 'processing', 'uploading', 'ingesting'].includes(status);
}
export function failureAdvice(error: string | null): string {
  const reason = (error ?? '').toLowerCase();
  if (reason.includes('recognition') || reason.includes('ocr')) return 'ocrAdvice';
  if (reason.includes('empty') || reason.includes('no text') || reason.includes('no extractable text')) return 'emptyFileAdvice';
  if (reason.includes('format') || reason.includes('unsupported')) return 'formatAdvice';
  if (reason.includes('size') || reason.includes('large')) return 'sizeAdvice';
  return 'generalFileAdvice';
}
export async function sha256(file: File): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}
