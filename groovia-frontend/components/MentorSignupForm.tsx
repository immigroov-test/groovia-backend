'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Card, CardBody } from './ui/Card';
import { Button } from './ui/Button';
import { Input } from './ui/Input';
import { createClient } from '../lib/supabase/client';

export function MentorSignupForm() {
  const router = useRouter();
  const [displayName, setDisplayName] = useState('');
  const [headline, setHeadline] = useState('');
  const [agreedMentor, setAgreedMentor] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const supabase = createClient();
      const { data: { user } } = await supabase.auth.getUser();
      const fullName = user?.user_metadata?.full_name || user?.user_metadata?.name;
      if (typeof fullName === 'string') setDisplayName(fullName);
    })();
  }, []);

  async function submit() {
    if (!displayName.trim() || !agreedMentor) return;
    setSubmitting(true);
    setError(null);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        setError('You need to be signed in.');
        return;
      }
      const res = await fetch('/api/mentor/signup', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({
          display_name: displayName.trim(),
          headline: headline.trim() || undefined,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          agreed_to_mentor_terms: agreedMentor,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Could not create your mentor profile. Please try again.');
        return;
      }
      router.refresh();
    } catch {
      setError('Could not create your mentor profile. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardBody className="pt-6 flex flex-col gap-4">
        <div>
          <h2 className="text-base font-semibold text-foreground">Become a mentor</h2>
          <p className="text-sm text-muted mt-1">
            Create your mentor profile, then connect your calendar so candidates can book sessions with you.
          </p>
        </div>
        <Input
          label="Display name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Jane Doe"
        />
        <Input
          label="Headline (optional)"
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="e.g. Software engineer who moved from India to the Netherlands"
        />
        <label className="text-xs text-muted flex items-start gap-2 select-none">
          <input
            type="checkbox"
            className="mt-0.5 accent-[--color-brand-500]"
            checked={agreedMentor}
            onChange={(e) => setAgreedMentor(e.target.checked)}
          />
          <span>
            I agree to the Mentor Terms, Data Processing Agreement, and commission structure, and
            consent to anonymised session insights being used to improve Groovia. (See{' '}
            <Link href="/mentor-terms" className="underline hover:text-foreground">Mentor Agreement</Link>.)
          </span>
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div>
          <Button variant="accent" onClick={submit} loading={submitting} disabled={!displayName.trim() || !agreedMentor}>
            Create mentor profile
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
