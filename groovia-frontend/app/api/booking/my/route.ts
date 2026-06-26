import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../lib/backend';

export async function GET(req: NextRequest) {
  const authHeader = req.headers.get('authorization');
  try {
    const res = await fetch(`${backendBaseUrl()}/booking/my`, {
      headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
