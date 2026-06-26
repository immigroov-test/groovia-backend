import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../lib/backend';

async function proxy(method: string, req: NextRequest, path = '/mentor/services') {
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
  const res = await fetch(`${backendBaseUrl()}${path}`, opts);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

export async function GET(req: NextRequest) {
  try { return await proxy('GET', req); }
  catch { return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 }); }
}

export async function POST(req: NextRequest) {
  try { return await proxy('POST', req); }
  catch { return NextResponse.json({ error: 'Failed to reach backend' }, { status: 502 }); }
}
