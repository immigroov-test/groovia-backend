import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../../lib/backend';

async function proxy(method: string, req: NextRequest, subpath: string) {
  const authHeader = req.headers.get('authorization');
  const opts: RequestInit = {
    method,
    headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
    cache: 'no-store',
  };
  if (method !== 'GET') {
    opts.body = await req.text();
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
  }
  const res = await fetch(`${backendBaseUrl()}/mentor/availability-v2${subpath}`, opts);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

function getSubpath(req: NextRequest, path: string[]): string {
  return path.length ? `/${path.join('/')}` : '';
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path = [] } = await params;
  try { return await proxy('GET', req, getSubpath(req, path)); }
  catch { return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 }); }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path = [] } = await params;
  try { return await proxy('POST', req, getSubpath(req, path)); }
  catch { return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 }); }
}
