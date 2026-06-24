import Link from 'next/link';
import { Card, CardBody } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { Mail } from 'lucide-react';

export default async function VerifyEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ email?: string }>;
}) {
  const { email } = await searchParams;

  return (
    <Card>
      <CardBody className="pt-7 text-center">
        <div className="mx-auto w-12 h-12 rounded-full bg-accent-50 flex items-center justify-center mb-4">
          <Mail className="h-6 w-6 text-accent-600" />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-brand-900">Check your inbox</h1>
        <p className="text-sm text-muted mt-2">
          We sent a confirmation link to{' '}
          <span className="font-medium text-foreground">{email ?? 'your email'}</span>.
          Click it to activate your account.
        </p>
        <p className="text-xs text-muted mt-4">
          Didn&apos;t arrive? Check spam, or try signing in — Supabase will resend if needed.
        </p>
        <div className="mt-6">
          <Link href="/login">
            <Button variant="outline">Back to sign in</Button>
          </Link>
        </div>
      </CardBody>
    </Card>
  );
}
