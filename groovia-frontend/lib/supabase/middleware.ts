// Supabase session refresh helper — runs on every request via middleware.ts.
import { createServerClient } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  // Without Supabase config we can't refresh a session. Degrade to guest instead of
  // 500-ing every matched route (MIDDLEWARE_INVOCATION_FAILED). Logs surface the cause.
  if (!supabaseUrl || !supabaseAnonKey) {
    console.error(
      '[middleware] Missing Supabase env vars — NEXT_PUBLIC_SUPABASE_URL/ANON_KEY not set at build time. Treating request as guest.',
    );
    return response;
  }

  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  // IMPORTANT: call getUser() (not getSession) — it revalidates the token with Supabase Auth.
  // A transient Auth/network failure must not take down every route, so treat it as guest.
  let user: Awaited<ReturnType<typeof supabase.auth.getUser>>['data']['user'] = null;
  try {
    const { data } = await supabase.auth.getUser();
    user = data.user;
  } catch (err) {
    console.error('[middleware] supabase.auth.getUser() failed; treating as guest:', err);
    return response;
  }

  const path = request.nextUrl.pathname;

  // Everyone — including guests — lands on /chat. / redirects there.
  if (path === '/') {
    const url = request.nextUrl.clone();
    url.pathname = '/chat';
    return NextResponse.redirect(url);
  }

  // /account is auth-only. /mentor is public — guests see the "Join as Mentor" signup form.
  if (!user && path.startsWith('/account')) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('next', path);
    return NextResponse.redirect(url);
  }

  // Logged-in users shouldn't see auth pages anymore — send them to chat.
  const guestOnlyPaths = ['/login', '/signup', '/forgot-password'];
  if (user && guestOnlyPaths.includes(path)) {
    const url = request.nextUrl.clone();
    url.pathname = '/chat';
    return NextResponse.redirect(url);
  }

  return response;
}
