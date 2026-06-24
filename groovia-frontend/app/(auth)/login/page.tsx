'use client';
import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

function LoginRedirect() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const p = new URLSearchParams();
    p.set('auth', 'open');
    p.set('mode', 'login');
    const next = params.get('next');
    if (next) p.set('next', next);
    router.replace(`/chat?${p.toString()}`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginRedirect />
    </Suspense>
  );
}
