import { NextRequest, NextResponse } from 'next/server';

export function GET(request: NextRequest) {
  const workspace = request.nextUrl.searchParams.get('workspace');
  const response = NextResponse.redirect(new URL('/dashboard/inbox', request.url));
  if (workspace && /^[0-9a-f-]{36}$/i.test(workspace)) {
    response.cookies.set('active_workspace', workspace, {
      path: '/', sameSite: 'lax', secure: request.nextUrl.protocol === 'https:',
    });
  }
  return response;
}
