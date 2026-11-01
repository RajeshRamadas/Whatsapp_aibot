import Link from 'next/link';
import messages from '@/messages/en.json';
export default function Home() {
  return <main id="main" className="mx-auto max-w-4xl p-6 sm:p-12">
    <h1 className="text-3xl font-semibold">{messages.home}</h1>
    <p className="mt-4 text-muted">{messages.homeDescription}</p>
    <Link className="mt-6 inline-flex min-h-11 items-center rounded-lg bg-accent px-5 text-accent-foreground" href="/dev/components">{messages.gallery}</Link>
  </main>;
}
