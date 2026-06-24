'use client';
import { useState } from 'react';
import Link from 'next/link';
import { createClient } from '../../../lib/supabase/client';
import { Card, CardBody } from '../../../components/ui/Card';
import { Input } from '../../../components/ui/Input';
import { Button } from '../../../components/ui/Button';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setLoading(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  return (
    <Card>
      <CardBody className="pt-7">
        {sent ? (
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight text-brand-900">Check your inbox</h1>
            <p className="text-sm text-muted mt-2">If <span className="font-medium text-foreground">{email}</span> matches an account, a reset link is on its way.</p>
            <div className="mt-6"><Link href="/chat?auth=open&mode=login"><Button variant="outline">Back to sign in</Button></Link></div>
          </div>
        ) : (
          <>
            <div className="text-center mb-6">
              <h1 className="text-2xl font-semibold tracking-tight text-brand-900">Forgot password?</h1>
              <p className="text-sm text-muted mt-1">We&apos;ll email you a reset link.</p>
            </div>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <Input
                label="Email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
              {error && <p className="text-xs text-red-600">{error}</p>}
              <Button type="submit" loading={loading}>Send reset link</Button>
            </form>
            <p className="mt-4 text-xs text-muted text-center">
              <Link href="/chat?auth=open&mode=login" className="hover:text-foreground">← Back to sign in</Link>
            </p>
          </>
        )}
      </CardBody>
    </Card>
  );
}
