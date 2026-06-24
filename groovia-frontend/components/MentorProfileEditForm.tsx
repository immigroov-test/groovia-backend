'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '../lib/supabase/client';
import { Card, CardBody } from './ui/Card';
import { Input } from './ui/Input';
import { Button } from './ui/Button';
import { MultiSelect } from './ui/MultiSelect';
import { COUNTRIES } from '../lib/countries';
import { LANGUAGES } from '../lib/languages';
import { cn } from '../lib/utils';

const COUNTRY_OPTIONS = COUNTRIES.map((c) => ({ value: c.code, label: c.name }));
const LANGUAGE_OPTIONS = LANGUAGES.map((l) => ({ value: l.code, label: l.name }));

const DOMAIN_OPTIONS = [
  'Software Engineering', 'Product Management', 'Data Science & AI', 'Design (UX/UI)',
  'Marketing', 'Sales', 'Finance & Banking', 'Healthcare', 'Legal', 'Education',
  'Entrepreneurship', 'Operations', 'HR & Recruiting', 'Consulting', 'Research',
  'Manufacturing', 'Real Estate', 'Media & Journalism', 'Government & Policy', 'Non-profit',
].map((d) => ({ value: d, label: d }));

const DURATION_OPTIONS = [
  { minutes: 30, label: '30 min' },
  { minutes: 60, label: '60 min' },
  { minutes: 90, label: '90 min' },
];

export interface MentorProfile {
  id: string;
  display_name: string;
  headline: string | null;
  bio: string | null;
  languages: string[] | null;
  linkedin_url: string | null;
  youtube_url: string | null;
  instagram_url: string | null;
  timezone: string | null;
  session_duration_minutes: number | null;
  expertise_country_codes: string[] | null;
  years_lived_experience: number | null;
  professional_domains: string[] | null;
  status: string;
}

interface Props {
  mentor: MentorProfile;
}

export function MentorProfileEditForm({ mentor }: Props) {
  const router = useRouter();

  // Non-critical fields
  const [displayName, setDisplayName] = useState(mentor.display_name);
  const [headline, setHeadline] = useState(mentor.headline ?? '');
  const [bio, setBio] = useState(mentor.bio ?? '');
  const [languages, setLanguages] = useState<string[]>(mentor.languages ?? []);
  const [sessionDuration, setSessionDuration] = useState(mentor.session_duration_minutes ?? 60);
  const [linkedinUrl, setLinkedinUrl] = useState(mentor.linkedin_url ?? '');
  const [youtubeUrl, setYoutubeUrl] = useState(mentor.youtube_url ?? '');
  const [instagramUrl, setInstagramUrl] = useState(mentor.instagram_url ?? '');

  // Critical fields
  const [countries, setCountries] = useState<string[]>(mentor.expertise_country_codes ?? []);
  const [yearsExp, setYearsExp] = useState(String(mentor.years_lived_experience ?? ''));
  const [domains, setDomains] = useState<string[]>(mentor.professional_domains ?? []);

  const [nonCriticalSuccess, setNonCriticalSuccess] = useState(false);
  const [criticalSuccess, setCriticalSuccess] = useState(false);
  const [nonCriticalError, setNonCriticalError] = useState<string | null>(null);
  const [criticalError, setCriticalError] = useState<string | null>(null);
  const [savingNonCritical, setSavingNonCritical] = useState(false);
  const [savingCritical, setSavingCritical] = useState(false);

  async function getToken(): Promise<string | null> {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token ?? null;
  }

  async function saveNonCritical(e: React.FormEvent) {
    e.preventDefault();
    setNonCriticalError(null);
    setNonCriticalSuccess(false);
    if (!displayName.trim()) { setNonCriticalError('Display name is required.'); return; }
    setSavingNonCritical(true);
    try {
      const token = await getToken();
      if (!token) { setNonCriticalError('Session expired. Please sign in again.'); return; }
      const res = await fetch('/api/mentor/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          display_name: displayName.trim(),
          headline: headline.trim() || null,
          bio: bio.trim() || null,
          languages,
          session_duration_minutes: sessionDuration,
          linkedin_url: linkedinUrl.trim() || null,
          youtube_url: youtubeUrl.trim() || null,
          instagram_url: instagramUrl.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) { setNonCriticalError(data.detail || 'Save failed. Please try again.'); return; }
      setNonCriticalSuccess(true);
      router.refresh();
    } catch {
      setNonCriticalError('Save failed. Please try again.');
    } finally {
      setSavingNonCritical(false);
    }
  }

  async function saveCritical(e: React.FormEvent) {
    e.preventDefault();
    setCriticalError(null);
    setCriticalSuccess(false);
    if (countries.length === 0) { setCriticalError('Select at least one country of expertise.'); return; }
    const years = parseInt(yearsExp, 10);
    if (!yearsExp || isNaN(years) || years < 0) { setCriticalError('Enter your years of lived experience.'); return; }
    setSavingCritical(true);
    try {
      const token = await getToken();
      if (!token) { setCriticalError('Session expired. Please sign in again.'); return; }
      const res = await fetch('/api/mentor/profile/critical', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          expertise_country_codes: countries,
          years_lived_experience: years,
          professional_domains: domains,
        }),
      });
      const data = await res.json();
      if (!res.ok) { setCriticalError(data.detail || 'Save failed. Please try again.'); return; }
      setCriticalSuccess(true);
      router.refresh();
    } catch {
      setCriticalError('Save failed. Please try again.');
    } finally {
      setSavingCritical(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Non-critical: basic info */}
      <form onSubmit={saveNonCritical}>
        <Card>
          <CardBody className="pt-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">Basic info</h2>
              <p className="text-sm text-muted mt-0.5">Changes here take effect immediately — no re-review needed.</p>
            </div>
            <Input
              label="Display name *"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
            />
            <Input
              label="Headline"
              value={headline}
              onChange={(e) => setHeadline(e.target.value)}
              placeholder="e.g. Software engineer who moved from India to the Netherlands"
            />
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium text-foreground">Bio</label>
              <textarea
                rows={4}
                value={bio}
                onChange={(e) => setBio(e.target.value)}
                className={cn(
                  'px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-y',
                  'placeholder:text-muted',
                  'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
                  'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25),0_1px_2px_rgba(15,23,42,0.04)]',
                )}
              />
            </div>
            <MultiSelect
              label="Languages you mentor in"
              options={LANGUAGE_OPTIONS}
              value={languages}
              onChange={setLanguages}
              placeholder="Select languages…"
            />
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-foreground">Session duration</span>
              <div className="flex gap-2">
                {DURATION_OPTIONS.map(({ minutes, label }) => (
                  <button
                    key={minutes}
                    type="button"
                    onClick={() => setSessionDuration(minutes)}
                    className={cn(
                      'flex-1 h-10 rounded-lg border text-sm font-medium transition-colors',
                      sessionDuration === minutes
                        ? 'bg-brand-600 border-brand-600 text-white'
                        : 'border-[--color-border] text-muted hover:text-foreground hover:border-brand-300',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <h3 className="text-sm font-medium text-foreground mb-2">Social links</h3>
              <div className="flex flex-col gap-3">
                <Input label="LinkedIn" type="url" value={linkedinUrl}
                  onChange={(e) => setLinkedinUrl(e.target.value)} placeholder="https://linkedin.com/in/…" />
                <Input label="YouTube" type="url" value={youtubeUrl}
                  onChange={(e) => setYoutubeUrl(e.target.value)} placeholder="https://youtube.com/@…" />
                <Input label="Instagram" type="url" value={instagramUrl}
                  onChange={(e) => setInstagramUrl(e.target.value)} placeholder="https://instagram.com/…" />
              </div>
            </div>
            {nonCriticalError && <p className="text-sm text-red-600">{nonCriticalError}</p>}
            {nonCriticalSuccess && <p className="text-sm text-green-600">Saved successfully.</p>}
            <div>
              <Button type="submit" loading={savingNonCritical}>Save changes</Button>
            </div>
          </CardBody>
        </Card>
      </form>

      {/* Critical fields — triggers re-approval */}
      <form onSubmit={saveCritical}>
        <Card>
          <CardBody className="pt-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">Expertise</h2>
              <p className="text-sm text-amber-600 mt-0.5">
                Changing these fields will reset your profile to <strong>pending review</strong> and our team
                will re-approve it before your profile goes live again.
              </p>
            </div>
            <MultiSelect
              label="Countries of expertise *"
              options={COUNTRY_OPTIONS}
              value={countries}
              onChange={setCountries}
              placeholder="Select countries…"
            />
            <Input
              label="Years of lived experience *"
              type="number"
              min={0}
              max={60}
              value={yearsExp}
              onChange={(e) => setYearsExp(e.target.value)}
            />
            <MultiSelect
              label="Professional domains"
              options={DOMAIN_OPTIONS}
              value={domains}
              onChange={setDomains}
              placeholder="Select domains…"
            />
            {criticalError && <p className="text-sm text-red-600">{criticalError}</p>}
            {criticalSuccess && (
              <p className="text-sm text-amber-600">
                Expertise updated. Your profile is now <strong>pending re-review</strong>.
              </p>
            )}
            <div>
              <Button type="submit" variant="outline" loading={savingCritical}>
                Save expertise (triggers re-review)
              </Button>
            </div>
          </CardBody>
        </Card>
      </form>

      <div className="text-center">
        <Link href="/mentor" className="text-sm text-muted hover:text-foreground underline underline-offset-2">
          ← Back to mentor hub
        </Link>
      </div>
    </div>
  );
}
