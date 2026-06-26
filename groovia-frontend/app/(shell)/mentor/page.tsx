import { redirect } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '../../../lib/supabase/server';
import { backendBaseUrl } from '../../../lib/backend';
import { Card, CardBody } from '../../../components/ui/Card';
import { MentorLanding } from '../../../components/MentorLanding';
import { ServicesManager } from '../../../components/ServicesManager';
import { AvailabilityManagerV2 } from '../../../components/AvailabilityManagerV2';
import { BookingManager } from '../../../components/BookingManager';

export const metadata = { title: 'Mentor Hub — Immigroov' };

interface MentorMe {
  id: string;
  slug: string;
  display_name: string;
  status: 'pending_review' | 'approved' | 'rejected' | 'suspended';
}

export default async function MentorPage() {
  const supabase = await createClient();
  const { data: { session } } = await supabase.auth.getSession();

  if (!session) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold tracking-tight text-brand-900">Become a mentor</h1>
          <p className="text-base text-muted mt-2">Help immigrants navigate their career journey.</p>
        </div>
        <MentorLanding />
      </div>
    );
  }

  const res = await fetch(`${backendBaseUrl()}/mentor/me`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    cache: 'no-store',
  });

  // No mentor row yet — redirect to onboarding.
  if (!res.ok) {
    redirect('/mentor/onboarding');
  }

  const mentor: MentorMe = await res.json();

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-brand-900">Mentor Hub</h1>
          <p className="text-sm text-muted mt-1">Welcome back, {mentor.display_name}.</p>
        </div>
        {mentor.status === 'approved' && (
          <div className="flex gap-2 shrink-0">
            <Link
              href="/mentor/profile"
              className="inline-flex items-center h-9 px-4 rounded-lg border border-[--color-border] text-sm font-medium text-muted hover:text-foreground hover:border-brand-300 transition-colors"
            >
              Edit profile
            </Link>
            <Link
              href="/mentor/availability"
              className="inline-flex items-center h-9 px-4 rounded-lg border border-[--color-border] text-sm font-medium text-muted hover:text-foreground hover:border-brand-300 transition-colors"
            >
              Set availability
            </Link>
          </div>
        )}
      </div>

      <div className="mt-8 flex flex-col gap-4">
        {mentor.status === 'pending_review' && (
          <>
            <Card>
              <CardBody className="pt-6">
                <h2 className="text-base font-semibold text-foreground">Application under review</h2>
                <p className="text-sm text-muted mt-1">
                  Thanks for applying. Our team is reviewing your profile — this usually takes 1–2 business days.
                  Use the time below to set your availability so it&apos;s ready the moment you&apos;re approved.
                </p>
              </CardBody>
            </Card>
            <div className="flex gap-2">
              <Link
                href="/mentor/availability"
                className="inline-flex items-center h-9 px-4 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 transition-colors"
              >
                Set availability
              </Link>
              <Link
                href="/mentor/profile"
                className="inline-flex items-center h-9 px-4 rounded-lg border border-[--color-border] text-sm font-medium text-muted hover:text-foreground hover:border-brand-300 transition-colors"
              >
                Edit profile
              </Link>
            </div>
          </>
        )}

        {mentor.status === 'rejected' && (
          <Card>
            <CardBody className="pt-6">
              <h2 className="text-base font-semibold text-foreground">Application not approved</h2>
              <p className="text-sm text-muted mt-1">
                Your mentor application wasn&apos;t approved this time. If you think this is a mistake or would
                like to update your profile and re-apply, please{' '}
                <Link href="/mentor/profile" className="text-brand-700 hover:underline">edit your profile</Link>{' '}
                or contact support.
              </p>
            </CardBody>
          </Card>
        )}

        {mentor.status === 'suspended' && (
          <Card>
            <CardBody className="pt-6">
              <h2 className="text-base font-semibold text-foreground">Account suspended</h2>
              <p className="text-sm text-muted mt-1">
                Your mentor account is currently suspended. Please contact support for more information.
              </p>
            </CardBody>
          </Card>
        )}

        {mentor.status === 'approved' && (
          <Card>
            <CardBody className="pt-6">
              <h2 className="text-base font-semibold text-foreground">Your profile is live</h2>
              <p className="text-sm text-muted mt-1">
                Mentees can discover you on the platform.{' '}
                <Link href={`/mentors/${mentor.slug}`} className="text-brand-700 hover:underline">
                  View public profile →
                </Link>
              </p>
            </CardBody>
          </Card>
        )}

        {mentor.status === 'approved' && (
          <Card>
            <CardBody className="pt-6 flex flex-col gap-4">
              <div>
                <h2 className="text-base font-semibold text-foreground">Your sessions</h2>
                <p className="text-sm text-muted mt-0.5">
                  Upcoming and past bookings. Approve requests, propose reschedules, or report a no-show.
                </p>
              </div>
              <BookingManager role="mentor" />
            </CardBody>
          </Card>
        )}

        {(mentor.status === 'approved' || mentor.status === 'pending_review') && (
          <>
            <Card>
              <CardBody className="pt-6 flex flex-col gap-4">
                <div>
                  <h2 className="text-base font-semibold text-foreground">Session types</h2>
                  <p className="text-sm text-muted mt-0.5">
                    Define the sessions mentees can book with you — duration, price, and intake questions.
                  </p>
                </div>
                <ServicesManager />
              </CardBody>
            </Card>

            <Card>
              <CardBody className="pt-6 flex flex-col gap-4">
                <div>
                  <h2 className="text-base font-semibold text-foreground">Schedule & availability</h2>
                  <p className="text-sm text-muted mt-0.5">
                    Set your weekly hours, block days off, and configure booking rules.
                  </p>
                </div>
                <AvailabilityManagerV2 />
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
