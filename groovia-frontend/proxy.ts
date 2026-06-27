import { type NextRequest } from 'next/server';
import { updateSession } from './lib/supabase/middleware';

// Next.js 16 renamed the "middleware" convention to "proxy". Proxy runs on the
// Node.js runtime (the legacy middleware.ts ran on Edge, which failed to boot
// the Supabase client → MIDDLEWARE_INVOCATION_FAILED). Do NOT add a `runtime`
// config option here — proxy throws if you do.
export async function proxy(request: NextRequest) {
  return await updateSession(request);
}

// Match only paths that need auth checks or session refresh.
// Public pages (/mentors, /privacy, /terms) and API routes don't need the proxy,
// which keeps invocations down and avoids unnecessary Supabase calls.
export const config = {
  matcher: [
    '/',
    '/chat',
    '/account/:path*',
    '/mentor/:path*',
    '/login',
    '/signup',
    '/verify-email',
    '/forgot-password',
    '/reset-password',
  ],
};
