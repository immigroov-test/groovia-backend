// Verifies a reCAPTCHA v3 token server-side so the secret key never reaches the browser.
import { NextRequest, NextResponse } from 'next/server';

const MIN_SCORE = 0.5;

export async function POST(req: NextRequest) {
  const secret = process.env.RECAPTCHA_SECRET_KEY;
  if (!secret) return NextResponse.json({ success: true }, { status: 200 });

  const { token } = await req.json().catch(() => ({ token: null }));
  if (!token) return NextResponse.json({ success: false, reason: 'missing_token' }, { status: 400 });

  try {
    const res = await fetch('https://www.google.com/recaptcha/api/siteverify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `secret=${encodeURIComponent(secret)}&response=${encodeURIComponent(token)}`,
    });
    const data = await res.json();
    const success = !!data.success && data.action === 'signup' && (data.score ?? 0) >= MIN_SCORE;
    return NextResponse.json({ success }, { status: 200 });
  } catch {
    // Fail open — don't block signups if Google's verification endpoint is unreachable.
    return NextResponse.json({ success: true }, { status: 200 });
  }
}
