import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../../lib/backend';

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ serviceId: string }> },
) {
  const { serviceId } = await params;
  try {
    const res = await fetch(`${backendBaseUrl()}/mentor/services/${serviceId}/questions`, {
      next: { revalidate: 30 },
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ serviceId: string }> },
) {
  const { serviceId } = await params;
  const authHeader = req.headers.get('authorization');
  const body = await req.text();
  try {
    const res = await fetch(`${backendBaseUrl()}/mentor/services/${serviceId}/questions`, {
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
