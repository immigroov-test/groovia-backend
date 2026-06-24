import { redirect } from 'next/navigation';
import { createClient } from '../../../../lib/supabase/server';
import { backendBaseUrl } from '../../../../lib/backend';
import { MentorProfileEditForm } from '../../../../components/MentorProfileEditForm';
import type { MentorProfile } from '../../../../components/MentorProfileEditForm';

export const metadata = { title: 'Edit Profile — Immigroov Mentor' };

export default async function MentorProfilePage() {
  const supabase = await createClient();
  const { data: { session } } = await supabase.auth.getSession();

  if (!session) {
    redirect(`/?auth=open&role=mentor&next=${encodeURIComponent('/mentor/profile')}`);
  }

  const res = await fetch(`${backendBaseUrl()}/mentor/me`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    cache: 'no-store',
  });

  if (!res.ok) {
    redirect('/mentor/onboarding');
  }

  const mentor: MentorProfile = await res.json();

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-brand-900">Edit profile</h1>
      <p className="text-sm text-muted mt-1">
        Keep your profile up to date. Critical expertise fields require re-approval.
      </p>
      <div className="mt-8">
        <MentorProfileEditForm mentor={mentor} />
      </div>
    </div>
  );
}
