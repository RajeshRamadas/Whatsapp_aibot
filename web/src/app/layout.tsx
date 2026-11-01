import './globals.css';
import type { Metadata } from 'next';
import messages from '@/messages/en.json';
export const metadata: Metadata = { title: messages.brand };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><a href="#main" className="sr-only focus:not-sr-only">{messages.back}</a>{children}</body></html>;
}
