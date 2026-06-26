'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { createClient } from '../lib/supabase/client';
import { Input } from './ui/Input';
import { Button } from './ui/Button';
import { GoogleButton } from './GoogleButton';
import { getRecaptchaToken } from '../lib/recaptcha';

export function MentorRegisterForm() {
  const router = useRouter();
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [agreedTerms, setAgreedTerms] = useState(false);
  const [agreedMentor, setAgreedMentor] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pendingVerification, setPendingVerification] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!agreedTerms) { setError('Please accept the Terms and Privacy Policy.'); return; }
    if (!agreedMentor) { setError('Please accept the Mentor Agreement.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setError(null);
    setLoading(true);

    const recaptchaToken = await getRecaptchaToken('signup').catch(() => null);
    if (recaptchaToken) {
      try {
        const res = await fetch('/api/auth/verify-recaptcha', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: recaptchaToken }),
        });
        const { success } = await res.json();
        if (!success) { setLoading(false); setError('Verification failed. Please try again.'); return; }
      } catch { /* fail open */ }
    }

    const fullName = `${firstName.trim()} ${lastName.trim()}`.trim();
    const supabase = createClient();
    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { full_name: fullName, role: 'mentor' },
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent('/mentor/onboarding')}`,
      },
    });
    setLoading(false);
    if (signUpError) {
      if (/already (registered|in use)/i.test(signUpError.message)) {
        setError('An account with this email already exists.');
        return;
      }
      setError(signUpError.message);
      return;
    }
    if (data.session) {
      router.push('/mentor/onboarding');
      return;
    }
    setPendingVerification(true);
  }

  // When the verification email is clicked in another tab, Supabase writes the session
  // to localStorage. onAuthStateChange fires in THIS tab too, navigating the user forward
  // without them having to manually switch tabs.
  useEffect(() => {
    if (!pendingVerification) return;
    const supabase = createClient();
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' && session) {
        router.push('/mentor/onboarding');
      }
    });
    return () => subscription.unsubscribe();
  }, [pendingVerification, router]);

  if (pendingVerification) {
    return (
      <div className="bg-card rounded-2xl shadow-sm border border-[--color-border] p-8 text-center flex flex-col gap-4">
        <h2 className="text-xl font-semibold text-brand-900">Check your inbox</h2>
        <p className="text-sm text-muted">
          We sent a verification link to{' '}
          <strong className="text-foreground">{email}</strong>. Click it to confirm your email —
          you&apos;ll then be taken to set up your mentor profile.
        </p>
        <Link
          href="/?auth=open&role=mentor&mode=login"
          className="text-sm text-brand-700 hover:underline"
        >
          Already verified? Login →
        </Link>
      </div>
    );
  }

  return (
    <div className="bg-card rounded-2xl shadow-sm border border-[--color-border] px-6 py-7 flex flex-col gap-4">
      <GoogleButton label="Continue with Google" next="/mentor/onboarding" />

      <div className="flex items-center gap-3 text-xs text-muted">
        <div className="h-px flex-1 bg-[--color-border]" />
        <span>or with email</span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Input label="First name" type="text" required value={firstName}
            onChange={(e) => setFirstName(e.target.value)} autoComplete="given-name" />
          <Input label="Last name" type="text" required value={lastName}
            onChange={(e) => setLastName(e.target.value)} autoComplete="family-name" />
        </div>
        <Input label="Email" type="email" required value={email}
          onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        <Input label="Password" type="password" required minLength={8} value={password}
          onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
          hint="At least 8 characters." />
        <Input label="Confirm password" type="password" required minLength={8}
          value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
          autoComplete="new-password" />

        <label className="text-xs text-muted flex items-start gap-2 select-none">
          <input type="checkbox" className="mt-0.5 accent-[--color-brand-500]"
            checked={agreedTerms} onChange={(e) => setAgreedTerms(e.target.checked)} />
          <span>
            I agree to the{' '}
            <Link href="/terms" className="underline hover:text-foreground">Terms</Link> and{' '}
            <Link href="/privacy" className="underline hover:text-foreground">Privacy Policy</Link>.
          </span>
        </label>
        <label className="text-xs text-muted flex items-start gap-2 select-none">
          <input type="checkbox" className="mt-0.5 accent-[--color-brand-500]"
            checked={agreedMentor} onChange={(e) => setAgreedMentor(e.target.checked)} />
          <span>
            I agree to the Mentor Terms, Data Processing Agreement, commission structure,
            and consent to anonymised session insights improving Groovia.
          </span>
        </label>

        {error && (
          <p className="text-xs text-red-600">
            {error}{' '}
            {error.includes('already exists') && (
              <Link href="/?auth=open&role=mentor&mode=login" className="underline font-medium">
                Login instead
              </Link>
            )}
          </p>
        )}

        <Button type="submit" variant="accent" loading={loading}>
          Create mentor account
        </Button>
      </form>

      <p className="text-xs text-muted text-center">
        Already have an account?{' '}
        <Link href="/?auth=open&role=mentor&mode=login" className="text-brand-700 hover:underline">
          Login
        </Link>
      </p>
    </div>
  );
}
