import KnowledgeScreen from '@/components/knowledge-screen';

export default async function Knowledge({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const query = await searchParams;
  return <KnowledgeScreen initialView={query.view || 'documents'} />;
}
