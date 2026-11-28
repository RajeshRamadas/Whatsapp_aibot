import { client, type Template } from '@/api/campaigns';
import type { components } from '@/api/schema';

type FollowUpInput = components['schemas']['FollowUpInput'] | components['schemas']['FollowUpPause'];
export type FollowUpView = components['schemas']['FollowUpView'];

export const leadFollowUps = (workspaceId: string) => {
  const { get, write } = client(workspaceId);
  return {
    read: (signal: AbortSignal) => get<FollowUpView>('lead-follow-up', { signal }),
    save: (body: FollowUpInput) => write<FollowUpView>('lead-follow-up', body, 'PUT'),
    templates: async (signal: AbortSignal) => {
      const rows: Template[] = [];
      for (let offset = 0; ; offset += 200) {
        const page = await get<Template[]>(`whatsapp/templates?approved=true&limit=200&offset=${offset}`, { signal });
        rows.push(...page.filter(row => row.category === 'MARKETING' || row.category === 'UTILITY'));
        if (page.length < 200) return rows;
      }
    },
  };
};
