// Supabase session refresh helper — runs on every request via middleware.ts.
import { createServerClient } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
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
    },
  );

  // IMPORTANT: call getUser() (not getSession) — it revalidates the token with Supabase Auth.
  const { data: { user } } = await supabase.auth.getUser();

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
