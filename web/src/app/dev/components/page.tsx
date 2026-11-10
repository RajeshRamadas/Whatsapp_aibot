import { getCopy } from '@/i18n/server';
import { notFound } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Badge, Card, ChatBubble, Input } from '@/components/ui/primitives';
import { ThemeToggle } from '@/components/theme-toggle';

export default async function Gallery() {
  const messages = await getCopy('base');
  if (process.env.NODE_ENV === 'production') notFound();
  return <main id="main" className="mx-auto grid max-w-5xl gap-6 p-4 sm:p-10">
    <header className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="text-3xl font-semibold">{messages.gallery}</h1><p className="text-muted">{messages.galleryDescription}</p></div><ThemeToggle label={messages.theme} /></header>
    <Card className="flex flex-wrap gap-3"><Button>{messages.primary}</Button><Button variant="secondary">{messages.secondary}</Button><Button variant="ghost">{messages.ghost}</Button><Button variant="destructive">{messages.destructive}</Button></Card>
    <Card><label className="mb-2 block" htmlFor="business">{messages.name}</label><Input id="business" placeholder={messages.name} /></Card>
    <Card className="flex flex-wrap gap-3"><Badge tone="success">{messages.active}</Badge><Badge tone="warning">{messages.waiting}</Badge><Badge tone="danger">{messages.failed}</Badge><Badge>{messages.closed}</Badge></Card>
    <Card className="grid gap-3"><ChatBubble kind="customer" label={messages.customer}>{messages.home}</ChatBubble><ChatBubble kind="bot" label={messages.bot}>{messages.homeDescription}</ChatBubble><ChatBubble kind="agent" label={messages.agent}>{messages.home}</ChatBubble><ChatBubble kind="note" label={messages.note}>{messages.noteDescription}</ChatBubble></Card>
    <Card><p>{messages.empty}</p><div className="mt-3 h-3 w-full overflow-hidden rounded-full bg-border"><div className="h-full w-1/3 bg-accent" /></div></Card>
  </main>;
}
