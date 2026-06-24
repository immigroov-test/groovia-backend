'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '../../../lib/supabase/client';
import { Card, CardBody } from '../../../components/ui/Card';
import { Input } from '../../../components/ui/Input';
import { Button } from '../../../components/ui/Button';

export default function ResetPasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.updateUser({ password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }
    router.push('/chat');
    router.refresh();
  }

  return (
    <Card>
      <CardBody className="pt-7">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-brand-900">Set a new password</h1>
          <p className="text-sm text-muted mt-1">Choose something memorable.</p>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input
            label="New password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            hint="At least 8 characters."
          />
          {error && <p className="text-xs text-red-600">{error}</p>}
          <Button type="submit" loading={loading}>Update password</Button>
        </form>
      </CardBody>
    </Card>
  );
}
