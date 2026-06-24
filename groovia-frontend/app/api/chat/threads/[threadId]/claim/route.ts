// BFF proxy: link a guest thread to the now-signed-in user.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../../lib/backend';

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await params;
  const authHeader = req.headers.get('authorization');
  if (!authHeader) return NextResponse.json({ claimed: false }, { status: 401 });
  try {
    const res = await fetch(`${backendBaseUrl()}/chat/threads/${threadId}/claim`, {
      method: 'POST',
      headers: { Authorization: authHeader },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ claimed: false }, { status: 502 });
  }
}
