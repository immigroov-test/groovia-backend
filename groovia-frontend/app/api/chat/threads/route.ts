// BFF proxy: list the user's chat threads.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../lib/backend';

export async function GET(req: NextRequest) {
  const authHeader = req.headers.get('authorization');
  if (!authHeader) {
    return NextResponse.json({ threads: [] }, { status: 401 });
  }
  try {
    const res = await fetch(`${backendBaseUrl()}/chat/threads`, {
      headers: { Authorization: authHeader },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ threads: [] }, { status: 502 });
  }
}
