import type { components } from '@/api/schema';
import { api } from '@/lib/api';

export type WorkspaceInput = components['schemas']['WorkspaceInput'];
export type IndustryTemplate = components['schemas']['IndustryTemplatePreview'];
export type TemplateText = components['schemas']['TemplateText'];

export function listIndustryTemplates(signal?: AbortSignal) {
  return api<IndustryTemplate[]>('workspaces/templates', { signal });
}

export function createWorkspace(input: WorkspaceInput) {
  return api<{ tenant_id: string }>('workspaces', { method: 'POST', body: JSON.stringify(input) });
}
