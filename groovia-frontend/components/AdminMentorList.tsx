'use client';
import { useState } from 'react';
import { createClient } from '../lib/supabase/client';
import { Card, CardBody } from './ui/Card';
import { Button } from './ui/Button';
import { Badge } from './ui/Badge';
import { UI_CONTENT } from '../lib/content';
import { COUNTRIES } from '../lib/countries';
import { LANGUAGES } from '../lib/languages';
import type { AdminMentor } from '../app/(shell)/admin/page';

const COUNTRY_MAP = Object.fromEntries(COUNTRIES.map((c) => [c.code, c.name]));
const LANGUAGE_MAP = Object.fromEntries(LANGUAGES.map((l) => [l.code, l.name]));
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

type Action = 'approve' | 'reject' | 'suspend' | 'reinstate';

interface ActionConfig {
  action: Action;
  label: string;
  variant: 'accent' | 'outline' | 'primary' | 'secondary' | 'ghost';
  loadingKey: string;
}

interface AvailabilitySlot {
  day_of_week: number;
  start_time: string;
  end_time: string;
}

interface MentorDetail extends AdminMentor {
  bio?: string | null;
  expertise_country_codes?: string[];
  languages?: string[];
  professional_domains?: string[];
  years_lived_experience?: number | null;
  linkedin_url?: string | null;
  youtube_url?: string | null;
  instagram_url?: string | null;
  session_duration_minutes?: number;
  timezone?: string | null;
  availability_slots?: AvailabilitySlot[];
}

interface Props {
  initialMentors: AdminMentor[];
  actions: ActionConfig[];
  removeOnAction?: boolean;
}

export function AdminMentorList({ initialMentors, actions, removeOnAction = true }: Props) {
  const [mentors, setMentors] = useState(initialMentors);
  const [pending, setPending] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [details, setDetails] = useState<Record<string, MentorDetail | null>>({});
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({});

  async function act(id: string, action: Action) {
    setPending((p) => ({ ...p, [id]: action }));
    setErrors((e) => { const n = { ...e }; delete n[id]; return n; });
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const res = await fetch(`/api/admin/mentors/${id}/${action}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token ?? ''}` },
      });
      if (res.ok) {
        if (removeOnAction) {
          setMentors((ms) => ms.filter((m) => m.id !== id));
        } else {
          const updated = await res.json();
          setMentors((ms) => ms.map((m) => m.id === id ? { ...m, status: updated.status } : m));
        }
      } else {
        const body = await res.json().catch(() => ({}));
        setErrors((e) => ({ ...e, [id]: body.detail || `Failed to ${action}. Please try again.` }));
      }
    } catch {
      setErrors((e) => ({ ...e, [id]: `Failed to ${action}. Please try again.` }));
    } finally {
      setPending((p) => { const n = { ...p }; delete n[id]; return n; });
    }
  }

  async function toggleDetail(id: string) {
    if (expanded[id]) {
      setExpanded((e) => ({ ...e, [id]: false }));
      return;
    }
    setExpanded((e) => ({ ...e, [id]: true }));
    if (details[id] !== undefined) return;
    setDetailLoading((l) => ({ ...l, [id]: true }));
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const res = await fetch(`/api/admin/mentors/${id}`, {
        headers: { Authorization: `Bearer ${session?.access_token ?? ''}` },
        cache: 'no-store',
      });
      const data = res.ok ? await res.json() : null;
      setDetails((d) => ({ ...d, [id]: data }));
    } catch {
      setDetails((d) => ({ ...d, [id]: null }));
    } finally {
      setDetailLoading((l) => ({ ...l, [id]: false }));
    }
  }

  const t = UI_CONTENT.admin;

  if (mentors.length === 0) {
    return <p className="text-sm text-muted">{t.empty}</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {mentors.map((mentor) => {
        const detail = details[mentor.id];
        const isExpanded = expanded[mentor.id];
        const isDetailLoading = detailLoading[mentor.id];

        return (
          <Card key={mentor.id}>
            <CardBody className="pt-6">
              {/* Header row */}
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-base font-semibold text-foreground">{mentor.display_name}</h2>
                    <Badge tone="neutral">{mentor.status.replace('_', ' ')}</Badge>
                    {mentor.submission_count > 1 && (
                      <Badge tone="warning">Re-submission #{mentor.submission_count}</Badge>
                    )}
                  </div>
                  {mentor.headline && (
                    <p className="text-sm text-muted mt-0.5">{mentor.headline}</p>
                  )}
                  <p className="text-xs text-muted mt-1">
                    {mentor.email ?? '—'}
                    {mentor.full_name ? ` · ${mentor.full_name}` : ''}
                  </p>
                  <p className="text-xs text-muted mt-0.5">
                    Applied {new Date(mentor.created_at).toLocaleDateString()}
                  </p>
                  {errors[mentor.id] && (
                    <p className="text-xs text-red-600 mt-1">{errors[mentor.id]}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0 flex-wrap">
                  <button
                    onClick={() => toggleDetail(mentor.id)}
                    className="inline-flex items-center h-8 px-3 rounded-lg border border-[--color-border] text-xs font-medium text-muted hover:text-foreground hover:border-brand-300 transition-colors"
                  >
                    {isExpanded ? 'Hide details' : 'View details'}
                  </button>
                  {actions.map(({ action, label, variant, loadingKey }) => (
                    <Button
                      key={action}
                      variant={variant}
                      size="sm"
                      loading={pending[mentor.id] === loadingKey}
                      disabled={!!pending[mentor.id]}
                      onClick={() => act(mentor.id, action)}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Detail panel */}
              {isExpanded && (
                <div className="mt-5 pt-5 border-t border-[--color-border]">
                  {isDetailLoading && (
                    <p className="text-sm text-muted">Loading profile…</p>
                  )}
                  {!isDetailLoading && !detail && (
                    <p className="text-sm text-red-600">Could not load profile details.</p>
                  )}
                  {!isDetailLoading && detail && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-4 text-sm">

                      {detail.bio && (
                        <div className="sm:col-span-2">
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Bio</p>
                          <p className="text-foreground leading-relaxed whitespace-pre-wrap">{detail.bio}</p>
                        </div>
                      )}

                      {detail.expertise_country_codes?.length ? (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Countries of expertise</p>
                          <p className="text-foreground">
                            {detail.expertise_country_codes
                              .map((c) => COUNTRY_MAP[c] ?? c)
                              .join(', ')}
                          </p>
                        </div>
                      ) : null}

                      {detail.years_lived_experience != null && (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Years lived abroad</p>
                          <p className="text-foreground">{detail.years_lived_experience} yr{detail.years_lived_experience !== 1 ? 's' : ''}</p>
                        </div>
                      )}

                      {detail.languages?.length ? (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Languages</p>
                          <p className="text-foreground">
                            {detail.languages.map((l) => LANGUAGE_MAP[l] ?? l).join(', ')}
                          </p>
                        </div>
                      ) : null}

                      {detail.professional_domains?.length ? (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Professional domains</p>
                          <p className="text-foreground">{detail.professional_domains.join(', ')}</p>
                        </div>
                      ) : null}

                      {detail.session_duration_minutes && (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Session duration</p>
                          <p className="text-foreground">{detail.session_duration_minutes} min</p>
                        </div>
                      )}

                      {detail.linkedin_url && (
                        <div>
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">LinkedIn</p>
                          <a href={detail.linkedin_url} target="_blank" rel="noopener noreferrer"
                            className="text-brand-700 hover:underline break-all">
                            {detail.linkedin_url}
                          </a>
                        </div>
                      )}

                      {detail.availability_slots && detail.availability_slots.length > 0 && (
                        <div className="sm:col-span-2">
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">Availability set</p>
                          <div className="flex flex-col gap-1">
                            {detail.availability_slots.map((s, i) => (
                              <p key={i} className="text-foreground text-xs">
                                {DAYS[s.day_of_week]} · {s.start_time} – {s.end_time}
                              </p>
                            ))}
                          </div>
                        </div>
                      )}

                      {(!detail.availability_slots || detail.availability_slots.length === 0) && (
                        <div className="sm:col-span-2">
                          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Availability</p>
                          <p className="text-muted text-xs">Not set yet</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}
