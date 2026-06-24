// BFF proxy: book a session with a mentor. Auth is optional — guests can book too.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../lib/backend';

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  const authHeader = req.headers.get('authorization');
  const body = await req.text();

  try {
    const res = await fetch(`${backendBaseUrl()}/mentors/${slug}/book`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body,
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
