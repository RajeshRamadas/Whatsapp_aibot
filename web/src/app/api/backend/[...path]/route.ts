import { NextRequest, NextResponse } from 'next/server';

const backend = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL('/api/v1/' + path.map(encodeURIComponent).join('/'), backend);
  url.search = request.nextUrl.search;
  const headers = new Headers(request.headers);
  const workspace = request.cookies.get('active_workspace')?.value;
  if (workspace && !headers.has('X-Workspace-Id')) headers.set('X-Workspace-Id', workspace);
  headers.delete('host');
  headers.delete('connection');
  headers.delete('content-length');
  const response = await fetch(url, {
    method: request.method,
    headers,
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
    redirect: 'manual',
    cache: 'no-store',
  });
  const outgoing = new NextResponse(response.body, { status: response.status });
  const type = response.headers.get('content-type');
  if (type) outgoing.headers.set('content-type', type);
  for (const name of ['location', 'content-disposition', 'cache-control', 'x-content-type-options',
    'content-range', 'accept-ranges', 'referrer-policy', 'retry-after', 'idempotency-replayed',
    'api-version', 'x-request-id', 'www-authenticate']) {
    const value = response.headers.get(name);
    if (value) outgoing.headers.set(name, name === 'location' && path[0] === 'public'
      && value.startsWith('/api/v1/public/') ? value.replace('/api/v1/public/', '/api/backend/public/') : value);
  }
  // Preserve multiple Set-Cookie headers for the session and CSRF double-submit cookie.
  for (const cookie of response.headers.getSetCookie()) outgoing.headers.append('set-cookie', cookie);
  return outgoing;
}
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as PUT, proxy as DELETE };
