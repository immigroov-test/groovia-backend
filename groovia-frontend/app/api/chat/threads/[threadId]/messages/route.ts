// BFF proxy: get messages for a specific thread.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../../lib/backend';

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await params;
  const authHeader = req.headers.get('authorization');
  if (!authHeader) {
    return NextResponse.json({ messages: [] }, { status: 401 });
  }
  try {
    const res = await fetch(`${backendBaseUrl()}/chat/threads/${threadId}/messages`, {
      headers: { Authorization: authHeader },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ messages: [] }, { status: 502 });
  }
}
