import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../../lib/backend';

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const authHeader = req.headers.get('authorization');
  if (!authHeader) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { id } = await params;
  try {
    const res = await fetch(`${backendBaseUrl()}/admin/mentors/${id}/approve`, {
      method: 'POST',
      headers: { Authorization: authHeader },
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
