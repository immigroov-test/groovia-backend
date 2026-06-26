import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../../lib/backend';

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ mentorId: string; serviceId: string }> },
) {
  const { mentorId, serviceId } = await params;
  const { searchParams } = new URL(req.url);
  const from = searchParams.get('from_date') || '';
  const to   = searchParams.get('to_date')   || '';
  const qs   = new URLSearchParams();
  if (from) qs.set('from_date', from);
  if (to)   qs.set('to_date', to);

  try {
    const res = await fetch(
      `${backendBaseUrl()}/booking/slots/${mentorId}/${serviceId}?${qs}`,
      { cache: 'no-store' },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
