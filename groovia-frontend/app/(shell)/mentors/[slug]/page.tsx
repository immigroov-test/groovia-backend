import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Card, CardBody } from '../../../../components/ui/Card';
import { Badge } from '../../../../components/ui/Badge';
import { DirectBookingWidget } from '../../../../components/DirectBookingWidget';
import type { Mentor } from '../../../../lib/types';
import { backendBaseUrl } from '../../../../lib/backend';

interface ServiceItem { id: string; title: string; duration: number; set_price: number; set_currency: string; }

async function fetchMentor(slug: string): Promise<Mentor | null> {
  try {
    const res = await fetch(`${backendBaseUrl()}/mentors/${slug}`, { next: { revalidate: 60 } });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

async function fetchServices(slug: string): Promise<ServiceItem[]> {
  try {
    const res = await fetch(`${backendBaseUrl()}/mentor/services/public/${slug}`, { next: { revalidate: 60 } });
    if (!res.ok) return [];
    const data = await res.json();
    return data.services ?? [];
  } catch { return []; }
}

export default async function MentorProfilePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const [mentor, services] = await Promise.all([fetchMentor(slug), fetchServices(slug)]);
  if (!mentor) notFound();

  const hasDirectBooking = services.length > 0;

  const profileBlock = (
    <header className="flex flex-col gap-3 mb-8">
      {mentor.photo_url && (
        <img
          src={mentor.photo_url}
          alt={mentor.display_name}
          className="h-20 w-20 rounded-full object-cover"
        />
      )}
      <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight text-brand-900">
        {mentor.display_name}
      </h1>
      {mentor.headline && (
        <p className="text-lg text-muted leading-relaxed">{mentor.headline}</p>
      )}
      <div className="flex flex-wrap gap-2 mt-2">
        {mentor.expertise_country_codes.map((c) => (
          <Badge key={c} tone="brand">{c}</Badge>
        ))}
        {(mentor.expertise_categories ?? []).map((cat) => (
          <Badge key={cat} tone="accent">{cat}</Badge>
        ))}
        {mentor.languages.map((lang) => (
          <Badge key={lang} tone="neutral">{lang.toUpperCase()}</Badge>
        ))}
      </div>
    </header>
  );

  // ── Direct booking (new system) ──────────────────────────────────────────────
  if (hasDirectBooking) {
    return (
      <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10">
        <Link href="/mentors" className="text-sm text-muted hover:text-foreground inline-flex items-center gap-1 mb-8">
          ← All mentors
        </Link>

        <div className="grid lg:grid-cols-[320px_1fr] gap-8 items-start">
          <div>
            {profileBlock}
            {mentor.bio && (
              <Card>
                <CardBody className="pt-5">
                  <h2 className="text-sm font-semibold text-foreground mb-2">About</h2>
                  <p className="text-sm text-foreground/80 leading-relaxed whitespace-pre-line">
                    {mentor.bio}
                  </p>
                </CardBody>
              </Card>
            )}
          </div>

          <div>
            <DirectBookingWidget
              mentor={{
                id:           mentor.id,
                slug:         mentor.slug,
                display_name: mentor.display_name,
                headline:     mentor.headline ?? null,
                bio:          mentor.bio ?? null,
                photo_url:    mentor.photo_url ?? null,
              }}
            />
          </div>
        </div>
      </div>
    );
  }

  // ── Fallback: profile view, no booking yet ───────────────────────────────────
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
      <Link href="/mentors" className="text-sm text-muted hover:text-foreground inline-flex items-center mb-6">
        ← All mentors
      </Link>

      {profileBlock}

      {mentor.bio && (
        <Card className="mb-6">
          <CardBody className="pt-6">
            <h2 className="text-base font-semibold text-foreground mb-2">About</h2>
            <p className="text-sm text-foreground/80 leading-relaxed whitespace-pre-line">{mentor.bio}</p>
          </CardBody>
        </Card>
      )}

      <Card className="border-dashed">
        <CardBody className="pt-6">
          <h2 className="text-base font-semibold text-foreground">Book a 1-on-1 session</h2>
          <p className="text-sm text-muted mt-1">
            {mentor.display_name.split(' ')[0]} is setting up their calendar. Check back soon.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
