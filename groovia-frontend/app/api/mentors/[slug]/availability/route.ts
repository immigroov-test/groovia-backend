// BFF proxy: live availability for a mentor's connected calendar.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../lib/backend';

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  const duration = req.nextUrl.searchParams.get('duration');
  const url = new URL(`${backendBaseUrl()}/mentors/${slug}/availability`);
  if (duration) url.searchParams.set('duration', duration);

  try {
    const res = await fetch(url, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 });
  }
}
