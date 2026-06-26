import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../lib/backend';

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ bookingId: string }> },
) {
  const { bookingId } = await params;
  const authHeader = req.headers.get('authorization');
  try {
    const res = await fetch(`${backendBaseUrl()}/booking/${bookingId}/requests`, {
      headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
