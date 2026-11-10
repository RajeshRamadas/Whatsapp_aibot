import en from '@/messages/inbox.en.json';
import hi from '@/messages/inbox.hi.json';
export const dynamic = 'force-static';
export function GET() {
  return new Response('self.inboxMessages = ' + JSON.stringify({ en, hi }) + ';', {
    headers: { 'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'public, max-age=3600' },
  });
}
