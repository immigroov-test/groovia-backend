'use client';
import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

function SignupRedirect() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const p = new URLSearchParams();
    p.set('auth', 'open');
    const next = params.get('next');
    if (next) p.set('next', next);
    const role = params.get('role');
    if (role === 'mentor') p.set('role', 'mentor');
    router.replace(`/chat?${p.toString()}`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupRedirect />
    </Suspense>
  );
}
