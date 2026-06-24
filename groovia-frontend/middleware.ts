import { type NextRequest } from 'next/server';
import { updateSession } from './lib/supabase/middleware';

export async function middleware(request: NextRequest) {
  return await updateSession(request);
}

// Match only paths that need auth checks or session refresh.
// Public pages (/mentors, /privacy, /terms) and API routes don't need middleware,
// which keeps Vercel middleware invocations down and avoids unnecessary Supabase calls.
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
