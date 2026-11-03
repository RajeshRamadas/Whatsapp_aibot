import { ImageResponse } from 'next/og';

export async function GET(_request: Request, context: { params: Promise<{ size: string }> }) {
  const { size: requested } = await context.params;
  if (!['192', '512'].includes(requested)) return new Response('Not found', { status: 404 });
  const size = Number(requested);
  return new ImageResponse(
    <div style={{ display: 'flex', width: '100%', height: '100%', background: '#064e3b',
      alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ display: 'flex', width: '55%', height: '42%', background: '#ffffff',
        borderRadius: size / 20, alignItems: 'center', justifyContent: 'center', gap: size / 20 }}>
        {[0, 1, 2].map(value => <div key={value} style={{ width: size / 16, height: size / 16,
          borderRadius: size / 32, background: '#064e3b' }} />)}
      </div>
    </div>, { width: size, height: size, headers: { 'Cache-Control': 'public, max-age=86400' } },
  );
}
