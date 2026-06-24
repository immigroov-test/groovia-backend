// BFF proxy: get the Nylas Hosted Auth URL for the logged-in mentor.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../lib/backend';

export async function GET(req: NextRequest) {
  const authHeader = req.headers.get('authorization');
  if (!authHeader) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const redirectUri = req.nextUrl.searchParams.get('redirect_uri');
  if (!redirectUri) return NextResponse.json({ error: 'Missing redirect_uri' }, { status: 400 });

  try {
    const url = `${backendBaseUrl()}/mentor/nylas/connect-url?redirect_uri=${encodeURIComponent(redirectUri)}`;
    const res = await fetch(url, {
      headers: { Authorization: authHeader },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
