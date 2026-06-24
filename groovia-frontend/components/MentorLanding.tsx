'use client';
import { Fragment } from 'react';
import { useRouter } from 'next/navigation';
import {
  BadgeCheck,
  BookOpen,
  CalendarCheck,
  ChevronRight,
  DollarSign,
  UserPlus,
  User,
  ShieldCheck,
  Calendar,
  Rocket,
} from 'lucide-react';
import { Button } from './ui/Button';

const BENEFITS = [
  {
    icon: BadgeCheck,
    iconBg: 'bg-blue-100',
    iconColor: 'text-blue-600',
    title: 'Build your personal brand',
    description: 'Showcase your expertise and stand out as a trusted voice in your field.',
  },
  {
    icon: BookOpen,
    iconBg: 'bg-sky-100',
    iconColor: 'text-sky-600',
    title: 'Share your hard-earned experience',
    description: 'Help others with insights from your real-life immigration and career journey.',
  },
  {
    icon: DollarSign,
    iconBg: 'bg-amber-100',
    iconColor: 'text-amber-600',
    title: 'Monetize your time & knowledge',
    description: 'Turn your valuable experience into a meaningful earning opportunity.',
  },
  {
    icon: CalendarCheck,
    iconBg: 'bg-teal-100',
    iconColor: 'text-teal-600',
    title: 'Flexible scheduling',
    description: 'Set your own availability and guide others at your own pace.',
  },
];

const STEPS = [
  {
    icon: UserPlus,
    label: 'Account',
    description: 'Create your account',
  },
  {
    icon: User,
    label: 'Profile',
    description: 'Your info & expertise',
  },
  {
    icon: ShieldCheck,
    label: 'Review',
    description: 'Admin approval',
  },
  {
    icon: Calendar,
    label: 'Availability',
    description: 'Set your schedule',
  },
  {
    icon: Rocket,
    label: 'Go Live',
    description: 'Accept bookings',
  },
];

export function MentorLanding() {
  const router = useRouter();

  function openSignup() {
    router.push('/mentor/register');
  }

  return (
    <div className="flex flex-col gap-16">
      {/* Why become a mentor */}
      <section>
        <h2 className="text-2xl font-semibold tracking-tight text-center text-brand-900">
          Why become a Mentor at Immigroov?
        </h2>
        <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
          {BENEFITS.map(({ icon: Icon, iconBg, iconColor, title, description }) => (
            <div
              key={title}
              className="flex gap-4 items-start p-5 rounded-2xl bg-white border border-[--color-border] shadow-sm"
            >
              <div className={`shrink-0 flex items-center justify-center w-12 h-12 rounded-xl ${iconBg}`}>
                <Icon className={`h-6 w-6 ${iconColor}`} />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">{title}</p>
                <p className="text-sm text-muted mt-0.5">{description}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section>
        <h2 className="text-2xl font-semibold tracking-tight text-center text-brand-900">
          How it works
        </h2>
        <div className="mt-8 flex items-start justify-between">
          {STEPS.map(({ icon: Icon, label, description }, i) => (
            <Fragment key={label}>
              {i > 0 && (
                <div className="flex items-center pt-4 shrink-0">
                  <ChevronRight className="h-4 w-4 text-muted" />
                </div>
              )}
              <div className="flex flex-col items-center text-center flex-1 min-w-0">
                <div
                  className={`flex items-center justify-center w-12 h-12 rounded-full border-2 ${
                    i === 0
                      ? 'bg-brand-700 border-brand-700 text-white'
                      : 'bg-white border-[--color-border] text-muted'
                  }`}
                >
                  <Icon className="h-5 w-5" />
                </div>
                <p className={`mt-2 text-xs font-semibold ${i === 0 ? 'text-brand-700' : 'text-foreground'}`}>
                  {label}
                </p>
                <p className="mt-0.5 text-[11px] text-muted leading-tight px-1">{description}</p>
              </div>
            </Fragment>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="flex flex-col items-center gap-4">
        <p className="text-sm text-muted text-center">
          Ready to start? Create a free mentor account and begin the application process.
        </p>
        <Button variant="accent" onClick={openSignup}>Create mentor account</Button>
      </section>
    </div>
  );
}
