// Receives the OAuth redirect from Nylas after a mentor connects their calendar.
// No user session is available on this redirect — the signed `state` param (minted
// by /api/mentor/nylas/connect-url) is what ties this back to a mentor row.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../../lib/backend';

export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get('code');
  const state = req.nextUrl.searchParams.get('state');
  const errorParam = req.nextUrl.searchParams.get('error');
  const redirectUri = `${req.nextUrl.origin}/api/nylas/callback`;

  if (errorParam) {
    return NextResponse.redirect(`${req.nextUrl.origin}/mentor?error=${encodeURIComponent(errorParam)}`);
  }
  if (!code || !state) {
    return NextResponse.redirect(`${req.nextUrl.origin}/mentor?error=missing_code`);
  }

  try {
    const res = await fetch(`${backendBaseUrl()}/mentor/nylas/callback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, state, redirect_uri: redirectUri }),
      cache: 'no-store',
    });
    if (!res.ok) {
      return NextResponse.redirect(`${req.nextUrl.origin}/mentor?error=connect_failed`);
    }
    return NextResponse.redirect(`${req.nextUrl.origin}/mentor?connected=1`);
  } catch {
    return NextResponse.redirect(`${req.nextUrl.origin}/mentor?error=connect_failed`);
  }
}
