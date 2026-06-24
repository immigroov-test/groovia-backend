import { redirect } from 'next/navigation';
import { createClient } from '../../../../lib/supabase/server';
import { MentorRegisterForm } from '../../../../components/MentorRegisterForm';

export const metadata = { title: 'Become a Mentor — Immigroov' };

export default async function MentorRegisterPage() {
  const supabase = await createClient();
  const { data: { session } } = await supabase.auth.getSession();

  // Already logged in — send to onboarding (which redirects to hub if already a mentor)
  if (session) {
    redirect('/mentor/onboarding');
  }

  return (
    <div className="mx-auto max-w-md px-4 py-10">
      <div className="text-center mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-brand-900">Become a mentor</h1>
        <p className="text-sm text-muted mt-2">
          Step 1 of 2 — create your account. You&apos;ll complete your mentor profile next.
        </p>
      </div>
      <MentorRegisterForm />
    </div>
  );
}
