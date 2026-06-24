import { redirect } from 'next/navigation';
import { createClient } from '../../../../lib/supabase/server';
import { backendBaseUrl } from '../../../../lib/backend';
import { MentorOnboardingForm } from '../../../../components/MentorOnboardingForm';

export const metadata = { title: 'Mentor Onboarding — Immigroov' };

export default async function MentorOnboardingPage() {
  const supabase = await createClient();
  const { data: { session } } = await supabase.auth.getSession();

  if (!session) {
    redirect('/mentor/register');
  }

  // If they already have a mentor row, send them to the hub.
  const mentorRes = await fetch(`${backendBaseUrl()}/mentor/me`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    cache: 'no-store',
  });
  if (mentorRes.ok) {
    redirect('/mentor');
  }

  const defaultName: string = session.user.user_metadata?.full_name
    ?? session.user.user_metadata?.name
    ?? '';

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-brand-900">Create your mentor profile</h1>
      <p className="text-sm text-muted mt-1">
        Complete the form below and our team will review your application, usually within 1–2 business days.
      </p>
      <div className="mt-8">
        <MentorOnboardingForm defaultName={defaultName} />
      </div>
    </div>
  );
}
