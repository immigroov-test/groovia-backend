'use client';
import { Suspense, useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { X } from 'lucide-react';
import { createClient } from '../lib/supabase/client';
import { Input } from './ui/Input';
import { Button } from './ui/Button';
import { GoogleButton } from './GoogleButton';
import { getRecaptchaToken } from '../lib/recaptcha';
import { cn } from '../lib/utils';

type Role = 'candidate' | 'mentor';
type Mode = 'signup' | 'login' | 'forgot';

function AuthModalInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const isOpen = params.get('auth') === 'open';
  const paramRole = params.get('role') === 'mentor' ? 'mentor' : 'candidate';
  const paramMode: Mode = params.get('mode') === 'login' ? 'login' : 'signup';
  const next = params.get('next') ?? undefined;

  const [role, setRole] = useState<Role>(paramRole);
  const [mode, setMode] = useState<Mode>(paramMode);
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [agreedTerms, setAgreedTerms] = useState(false);
  const [agreedMentor, setAgreedMentor] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const [forgotSent, setForgotSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pendingVerification, setPendingVerification] = useState(false);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendSent, setResendSent] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setRole(paramRole);
      setMode(paramRole === 'mentor' ? 'login' : paramMode);
      setFirstName('');
      setLastName('');
      setEmail('');
      setPassword('');
      setConfirmPassword('');
      setAgreedTerms(false);
      setAgreedMentor(false);
      setForgotEmail('');
      setForgotSent(false);
      setError(null);
      setPendingVerification(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  function close() {
    const p = new URLSearchParams(params.toString());
    p.delete('auth');
    p.delete('role');
    p.delete('mode');
    p.delete('next');
    const qs = p.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  function switchMode(m: Mode) {
    setMode(m);
    setError(null);
    setForgotSent(false);
    if (m === 'forgot') setForgotEmail(email);
  }

  const isMentor = role === 'mentor';

  async function handleSignup(e: React.FormEvent) {
    e.preventDefault();
    if (!agreedTerms) { setError('Please accept the Terms and Privacy Policy.'); return; }
    if (isMentor && !agreedMentor) { setError('Please accept the Mentor Agreement.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setError(null);
    setLoading(true);

    const recaptchaToken = await getRecaptchaToken('signup').catch(() => null);
    if (recaptchaToken) {
      try {
        const r = await fetch('/api/auth/verify-recaptcha', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: recaptchaToken }),
        });
        const { success } = await r.json();
        if (!success) { setLoading(false); setError('Verification failed. Please try again.'); return; }
      } catch { /* fail open */ }
    }

    const fullName = `${firstName.trim()} ${lastName.trim()}`.trim();
    const supabase = createClient();
    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { full_name: fullName, role },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    setLoading(false);
    if (signUpError) {
      if (/already (registered|in use)/i.test(signUpError.message)) {
        setError('An account with this email already exists. Sign in instead?');
        return;
      }
      setError(signUpError.message);
      return;
    }
    if (data.session) {
      window.location.href = isMentor ? '/mentor' : (next ?? '/chat');
      return;
    }
    setPendingVerification(true);
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const { error: loginError } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (loginError) {
      if (/email not confirmed/i.test(loginError.message)) {
        setPendingVerification(true);
        return;
      }
      setError(loginError.message);
      return;
    }
    window.location.href = isMentor ? '/mentor/onboarding' : (next ?? '/chat');
  }

  async function handleResend() {
    setResendLoading(true);
    setResendSent(false);
    const supabase = createClient();
    await supabase.auth.resend({ type: 'signup', email });
    setResendLoading(false);
    setResendSent(true);
  }

  async function handleForgotPassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(forgotEmail, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setLoading(false);
    if (resetError) { setError(resetError.message); return; }
    setForgotSent(true);
  }

  if (!isOpen) return null;

  const googleNext = isMentor ? '/mentor' : next;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-brand-900/50 backdrop-blur-sm">
      <div
        className="flex min-h-full items-center justify-center p-4"
        onClick={(e) => { if (e.target === e.currentTarget) close(); }}
      >
      <div className="relative w-full max-w-md bg-card rounded-2xl shadow-2xl animate-fade-up">
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="absolute top-4 right-4 z-10 p-1.5 rounded-full text-muted hover:text-foreground hover:bg-black/5 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="px-6 sm:px-7 pt-7 pb-6">
          {pendingVerification ? (
            <div className="flex flex-col gap-4">
              <div>
                <h2 className="text-xl font-semibold tracking-tight text-brand-900">Verify your email</h2>
                <p className="text-sm text-muted mt-2">
                  A verification link was sent to <b className="text-foreground">{email}</b>.
                  Click it to confirm your account
                  {isMentor ? ' and complete your mentor profile.' : '.'}
                </p>
                <p className="text-xs text-muted mt-1">Don&apos;t see it? Check your spam folder.</p>
              </div>
              {resendSent ? (
                <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                  Verification email resent. Check your inbox.
                </p>
              ) : (
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={resendLoading}
                  className="text-sm text-brand-700 hover:underline disabled:opacity-50 text-left"
                >
                  {resendLoading ? 'Sending…' : 'Resend verification email'}
                </button>
              )}
              <button
                type="button"
                onClick={() => { setPendingVerification(false); switchMode('login'); }}
                className="text-xs text-muted hover:text-foreground text-left"
              >
                ← Back to sign in
              </button>
            </div>
          ) : mode === 'forgot' ? (
            <div className="flex flex-col gap-4">
              <div className="text-center">
                <h2 className="text-2xl font-semibold tracking-tight text-brand-900">Forgot password?</h2>
                <p className="text-sm text-muted mt-1">We&apos;ll email you a reset link.</p>
              </div>
              {forgotSent ? (
                <div className="flex flex-col gap-4 text-center">
                  <p className="text-sm text-muted">
                    If <span className="font-medium text-foreground">{forgotEmail}</span> matches an account,
                    a reset link is on its way. Check your spam folder if it doesn&apos;t arrive within a
                    few minutes.
                  </p>
                  <Button variant="outline" onClick={() => switchMode('login')}>
                    Back to sign in
                  </Button>
                </div>
              ) : (
                <form onSubmit={handleForgotPassword} className="flex flex-col gap-3">
                  <Input
                    label="Email"
                    type="email"
                    required
                    value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)}
                    autoComplete="email"
                  />
                  {error && <p className="text-xs text-red-600">{error}</p>}
                  <Button type="submit" loading={loading}>Send reset link</Button>
                  <button
                    type="button"
                    onClick={() => switchMode('login')}
                    className="text-xs text-muted text-center hover:text-foreground"
                  >
                    ← Back to sign in
                  </button>
                </form>
              )}
            </div>
          ) : (
            <>
              <div className="text-center mb-5">
                <h2 className="text-2xl font-semibold tracking-tight text-brand-900">
                  {mode === 'signup' ? 'Create your account' : 'Welcome back'}
                </h2>
                <p className="text-sm mt-1">
                  {mode === 'signup'
                    ? (isMentor
                        ? <span className="text-muted">Join Immigroov as a mentor.</span>
                        : <span className="font-semibold text-emerald-600">Free to start. No card required.</span>)
                    : <span className="text-muted">Sign in to continue your journey.</span>}
                </p>
              </div>

              {/* Role toggle */}
              <div className="mb-5 grid grid-cols-2 gap-1 rounded-xl bg-brand-100 p-1">
                {(['candidate', 'mentor'] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => { setRole(r); setError(null); if (r === 'mentor') setMode('login'); }}
                    className={cn(
                      'h-9 rounded-lg text-sm font-semibold transition-all',
                      role === r
                        ? 'bg-white text-brand-900 shadow-sm ring-1 ring-brand-200'
                        : 'text-brand-700 hover:text-brand-900 hover:bg-white/60',
                    )}
                  >
                    {r === 'candidate' ? 'User' : 'Mentor'}
                  </button>
                ))}
              </div>

              {/* Mode toggle — hidden for mentor; mentor signup goes through MentorLanding */}
              {!isMentor && (
                <div className="mb-5 flex rounded-lg overflow-hidden text-sm border border-brand-200">
                  {(['signup', 'login'] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => switchMode(m)}
                      className={cn(
                        'flex-1 h-10 font-semibold transition-all',
                        mode === m
                          ? 'bg-brand-900 text-white'
                          : 'bg-white text-brand-800 hover:bg-brand-50 hover:text-brand-900',
                      )}
                    >
                      {m === 'signup' ? 'Sign up' : 'Sign in'}
                    </button>
                  ))}
                </div>
              )}

              <GoogleButton label="Continue with Google" next={googleNext} />

              <div className="my-4 flex items-center gap-3 text-xs text-muted">
                <div className="h-px flex-1 bg-[--color-border]" />
                <span>or with email</span>
                <div className="h-px flex-1 bg-[--color-border]" />
              </div>

              {mode === 'signup' ? (
                <form onSubmit={handleSignup} className="flex flex-col gap-3">
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
                  {isMentor && (
                    <label className="text-xs text-muted flex items-start gap-2 select-none">
                      <input type="checkbox" className="mt-0.5 accent-[--color-brand-500]"
                        checked={agreedMentor} onChange={(e) => setAgreedMentor(e.target.checked)} />
                      <span>
                        I agree to the Mentor Terms, Data Processing Agreement, commission structure,
                        and consent to anonymised session insights improving Groovia.
                      </span>
                    </label>
                  )}
                  {error && (
                    <p className="text-xs text-red-600">
                      {error}{' '}
                      {error.includes('already exists') && (
                        <button type="button" onClick={() => switchMode('login')}
                          className="underline font-medium">Switch to sign in</button>
                      )}
                    </p>
                  )}
                  <Button type="submit" variant={isMentor ? 'accent' : 'primary'} loading={loading}>
                    {isMentor ? 'Create mentor account' : 'Create account'}
                  </Button>
                </form>
              ) : (
                <form onSubmit={handleLogin} className="flex flex-col gap-3">
                  <Input label="Email" type="email" required value={email}
                    onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
                  <Input label="Password" type="password" required value={password}
                    onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
                  {error && <p className="text-xs text-red-600">{error}</p>}
                  <Button type="submit" loading={loading}>Sign in</Button>
                  <button
                    type="button"
                    onClick={() => switchMode('forgot')}
                    className="text-xs text-muted text-center hover:text-foreground"
                  >
                    Forgot password?
                  </button>
                </form>
              )}
            </>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}

export function AuthModal() {
  return (
    <Suspense fallback={null}>
      <AuthModalInner />
    </Suspense>
  );
}
