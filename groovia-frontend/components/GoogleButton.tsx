'use client';
import { useState } from 'react';
import { createClient } from '../lib/supabase/client';
import { Button } from './ui/Button';
import { FEATURES } from '../lib/features';

export function GoogleButton({ label = 'Continue with Google', next }: { label?: string; next?: string }) {
  const [loading, setLoading] = useState(false);

  if (!FEATURES.googleOAuth) return null;

  async function signIn() {
    setLoading(true);
    const supabase = createClient();
    const redirectTo = `${window.location.origin}/auth/callback${next ? `?next=${encodeURIComponent(next)}` : ''}`;
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo },
    });
    if (error) {
      setLoading(false);
      alert(error.message);
    }
  }

  return (
    <Button onClick={signIn} variant="outline" loading={loading} className="w-full">
      <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden>
        <path fill="#EA4335" d="M12 5c1.6 0 3.1.6 4.2 1.6l3.2-3.1C17.4 1.6 14.9.5 12 .5 7.4.5 3.4 3.1 1.4 6.9l3.7 2.9C6 7 8.8 5 12 5z"/>
        <path fill="#4285F4" d="M23.5 12.2c0-.9-.1-1.7-.3-2.5H12v4.7h6.5c-.3 1.5-1.1 2.7-2.4 3.6l3.7 2.9c2.2-2 3.7-5 3.7-8.7z"/>
        <path fill="#FBBC05" d="M5.1 14.2c-.3-.8-.4-1.6-.4-2.2 0-.6.1-1.4.4-2.2L1.4 6.9C.5 8.5 0 10.2 0 12s.5 3.5 1.4 5.1l3.7-2.9z"/>
        <path fill="#34A853" d="M12 23.5c3.2 0 5.9-1.1 7.9-2.9l-3.7-2.9c-1 .7-2.4 1.2-4.2 1.2-3.2 0-6-2-7-4.7L1.4 17c2 3.8 6 6.5 10.6 6.5z"/>
      </svg>
      {label}
    </Button>
  );
}
