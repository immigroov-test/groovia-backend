'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '../lib/supabase/client';
import { Card, CardBody } from './ui/Card';
import { Input } from './ui/Input';
import { Button } from './ui/Button';
import { MultiSelect } from './ui/MultiSelect';
import { PhoneInput } from './ui/PhoneInput';
import { PhotoUpload } from './ui/PhotoUpload';
import { SocialLinks, type SocialLink } from './ui/SocialLinks';
import { CountrySelect } from './ui/CountrySelect';
import { COUNTRIES } from '../lib/countries';
import { LANGUAGES } from '../lib/languages';
import { COUNTRY_TIMEZONES } from '../lib/countryTimezones';
import { cn } from '../lib/utils';

const COUNTRY_OPTIONS = COUNTRIES.map((c) => ({ value: c.code, label: c.name }));
const LANGUAGE_OPTIONS = LANGUAGES.map((l) => ({ value: l.code, label: l.name }));

const DOMAIN_OPTIONS = [
  'Software Engineering', 'Product Management', 'Data Science & AI', 'Design (UX/UI)',
  'Marketing', 'Sales', 'Finance & Banking', 'Healthcare', 'Legal', 'Education',
  'Entrepreneurship', 'Operations', 'HR & Recruiting', 'Consulting', 'Research',
  'Manufacturing', 'Real Estate', 'Media & Journalism', 'Government & Policy', 'Non-profit',
].map((d) => ({ value: d, label: d }));

const BIO_MAX = 2000;
const NOTES_MAX = 500;

interface Props {
  defaultName?: string;
  userId?: string;
}

export function MentorOnboardingForm({ defaultName = '', userId = '' }: Props) {
  const router = useRouter();

  // — Profile fields
  const [displayName, setDisplayName] = useState(defaultName);
  const [professionalTitle, setProfessionalTitle] = useState('');
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [phone, setPhone] = useState('');

  // — About
  const [bio, setBio] = useState('');

  // — Location
  const [country, setCountry] = useState('');
  const [city, setCity] = useState('');
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone);

  // — Presence
  const [languages, setLanguages] = useState<string[]>([]);
  const [socialLinks, setSocialLinks] = useState<SocialLink[]>([]);
  const [publicNotes, setPublicNotes] = useState('');

  // — Expertise (critical fields — trigger re-approval)
  const [expertiseCountries, setExpertiseCountries] = useState<string[]>([]);
  const [yearsExp, setYearsExp] = useState('');
  const [domains, setDomains] = useState<string[]>([]);

  // — Agreement
  const [agreedMentor, setAgreedMentor] = useState(false);

  // — State
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function onCountryChange(code: string) {
    setCountry(code);
    const tz = COUNTRY_TIMEZONES[code];
    if (tz) setTimezone(tz);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!displayName.trim()) { setError('Display name is required.'); return; }
    if (!professionalTitle.trim()) { setError('Professional title is required.'); return; }
    if (!country) { setError('Please select your current country.'); return; }
    if (languages.length === 0) { setError('Select at least one language.'); return; }
    if (expertiseCountries.length === 0) { setError('Select at least one country of expertise.'); return; }
    if (expertiseCountries.length > 2) { setError('You can select a maximum of 2 countries of expertise.'); return; }
    const years = parseInt(yearsExp, 10);
    if (!yearsExp || isNaN(years) || years < 0) { setError('Enter your years of lived experience.'); return; }
    if (!agreedMentor) { setError('Please accept the Mentor Agreement to proceed.'); return; }

    setSubmitting(true);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) { setError('Session expired. Please sign in again.'); return; }

      const res = await fetch('/api/mentor/signup', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({
          display_name: displayName.trim(),
          headline: professionalTitle.trim() || undefined,
          photo_url: photoUrl || undefined,
          phone: phone || undefined,
          bio: bio.trim() || undefined,
          country,
          city: city.trim() || undefined,
          timezone,
          languages,
          social_links: socialLinks,
          public_notes: publicNotes.trim() || undefined,
          expertise_country_codes: expertiseCountries,
          years_lived_experience: years,
          professional_domains: domains,
          agreed_to_mentor_terms: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Something went wrong. Please try again.');
        return;
      }
      if (!data.id) {
        setError('Unexpected response from server. Please try again.');
        return;
      }
      router.push('/mentor');
      router.refresh();
    } catch {
      setError('Could not create your mentor profile. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">

      {/* ── Section 1: Profile ──────────────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-5">
          <div>
            <h2 className="text-base font-semibold text-foreground">Profile Settings</h2>
            <p className="text-sm text-muted mt-0.5">Basic info, expertise, languages</p>
          </div>

          {/* Photo */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2">
              <label className="text-sm font-medium text-foreground">Profile Photo</label>
              <span className="text-xs text-muted">(Recommended — helps mentees connect with you)</span>
            </div>
            <PhotoUpload value={photoUrl} onChange={setPhotoUrl} userId={userId} />
          </div>

          {/* Display name */}
          <Input
            label="Full name *"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g. Priya Nair"
            required
          />

          {/* Professional title */}
          <Input
            label="Professional Title *"
            value={professionalTitle}
            onChange={(e) => setProfessionalTitle(e.target.value)}
            placeholder="e.g. Supply Chain Professional, Software Engineer, AI Developer, Career Expert"
            required
            hint="How you'll appear to mentees — your role or area of expertise."
          />

          {/* Phone */}
          <PhoneInput
            label="Phone Number *"
            value={phone}
            onChange={setPhone}
            required
            hint="Used for urgent session coordination only. Not shown publicly."
          />
        </CardBody>
      </Card>

      {/* ── Section 2: About You ─────────────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">About You *</h2>
            <p className="text-sm text-muted mt-0.5">
              Introduce yourself — share your experience, skills, and approach to helping clients.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <textarea
              rows={5}
              value={bio}
              onChange={(e) => setBio(e.target.value.slice(0, BIO_MAX))}
              placeholder="Tell mentees about your immigration journey, your current role, and how you can help them…"
              className={cn(
                'px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-y',
                'placeholder:text-muted',
                'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
                'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25),0_1px_2px_rgba(15,23,42,0.04)]',
              )}
            />
            <p className={cn('text-xs text-right', bio.length >= BIO_MAX ? 'text-red-500' : 'text-muted')}>
              {bio.length}/{BIO_MAX}
            </p>
          </div>
        </CardBody>
      </Card>

      {/* ── Section 3: Location & Languages ─────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">Location & Languages</h2>
          </div>

          {/* Country (where they live now) */}
          <CountrySelect
            label="Country *"
            value={country}
            onChange={onCountryChange}
            required
            placeholder="Select your current country…"
            hint="Country you currently live in."
          />

          {/* City */}
          <Input
            label="City"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            placeholder="e.g. Amsterdam"
            hint="Optional — shown on your public profile."
          />

          {/* Timezone — auto-filled from country, editable */}
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-foreground">Timezone</label>
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className={cn(
                'h-10 px-3 rounded-lg bg-white text-sm text-foreground',
                'shadow-[0_0_0_1px_rgba(15,23,42,0.06)]',
                'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]',
              )}
            >
              {Intl.supportedValuesOf('timeZone').map((tz) => (
                <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>
              ))}
            </select>
            <p className="text-xs text-muted">Auto-selected from your country. You can adjust it.</p>
          </div>

          {/* Languages */}
          <MultiSelect
            label="Languages Spoken *"
            options={LANGUAGE_OPTIONS}
            value={languages}
            onChange={setLanguages}
            placeholder="Type to search (e.g. "Ja" → Japanese)…"
            hint="Start typing to filter; select to add."
          />
        </CardBody>
      </Card>

      {/* ── Section 4: Social Links ──────────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">Social Links</h2>
            <p className="text-sm text-muted mt-0.5">
              Add links to your public profiles — helps mentees learn more about you.
            </p>
          </div>
          <SocialLinks value={socialLinks} onChange={setSocialLinks} hint="Optional — up to one link per platform." />
        </CardBody>
      </Card>

      {/* ── Section 5: Expertise ─────────────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">Expertise</h2>
            <p className="text-sm text-muted mt-0.5">
              These fields determine which mentees you can best serve. Changes here will require
              re-approval by our team.
            </p>
          </div>

          <MultiSelect
            label="Countries of Expertise * (max 2)"
            options={COUNTRY_OPTIONS}
            value={expertiseCountries}
            onChange={setExpertiseCountries}
            placeholder="Select up to 2 countries…"
            maxSelected={2}
            hint="Countries you have direct immigration or career experience in."
          />

          <Input
            label="Years of Lived Experience *"
            type="number"
            min={0}
            max={60}
            value={yearsExp}
            onChange={(e) => setYearsExp(e.target.value)}
            placeholder="e.g. 5"
            hint="Total years you have lived or worked abroad as an immigrant."
          />

          <MultiSelect
            label="Professional Domains"
            options={DOMAIN_OPTIONS}
            value={domains}
            onChange={setDomains}
            placeholder="Select domains…"
            hint="Industries or roles you can advise on."
          />
        </CardBody>
      </Card>

      {/* ── Section 6: Public Notes ──────────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">Public Notes</h2>
            <p className="text-sm text-muted mt-0.5">
              Instructions, disclaimers, or anything clients should know before booking.
              Visible on your profile.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <textarea
              rows={3}
              value={publicNotes}
              onChange={(e) => setPublicNotes(e.target.value.slice(0, NOTES_MAX))}
              placeholder="e.g. I only accept sessions in English. I specialise in Netherlands visa pathways."
              className={cn(
                'px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-y',
                'placeholder:text-muted',
                'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
                'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]',
              )}
            />
            <p className={cn('text-xs text-right', publicNotes.length >= NOTES_MAX ? 'text-red-500' : 'text-muted')}>
              {publicNotes.length}/{NOTES_MAX}
            </p>
          </div>
        </CardBody>
      </Card>

      {/* ── Section 7: Agreement & Submit ────────────────────────── */}
      <Card>
        <CardBody className="pt-6 flex flex-col gap-4">
          <label className="text-sm text-muted flex items-start gap-2 select-none cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5 accent-[--color-brand-500]"
              checked={agreedMentor}
              onChange={(e) => setAgreedMentor(e.target.checked)}
            />
            <span>
              I agree to the{' '}
              <Link href="/mentor-terms" className="underline hover:text-foreground">Mentor Agreement</Link>,
              Data Processing Agreement, and commission structure. I consent to anonymised session
              insights being used to improve Groovia.
            </span>
          </label>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button type="submit" variant="accent" loading={submitting}>
            Submit application
          </Button>
          <p className="text-xs text-muted">
            Your profile will be reviewed by our team before going live. This usually takes 1–2 business days.
          </p>
        </CardBody>
      </Card>
    </form>
  );
}
