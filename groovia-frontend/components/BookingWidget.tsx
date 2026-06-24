'use client';
import { useEffect, useMemo, useState } from 'react';
import { CalendarCheck, ChevronLeft, ChevronRight, Clock, Globe, Loader2, Video } from 'lucide-react';
import { Button } from './ui/Button';
import { Input } from './ui/Input';
import { createClient } from '../lib/supabase/client';
import { cn } from '../lib/utils';
import { UI_CONTENT } from '../lib/content';

const C = UI_CONTENT.booking;

// UTC date string for Google Calendar: "YYYYMMDDTHHmmssZ"
function toGCalDate(unix: number): string {
  return new Date(unix * 1000).toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
}

function gcalUrl(start: number, end: number, title: string, meetUrl: string | null): string {
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: title,
    dates: `${toGCalDate(start)}/${toGCalDate(end)}`,
    ...(meetUrl ? { details: `Video call: ${meetUrl}`, location: meetUrl } : {}),
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

interface Props {
  slug: string;
  mentorName: string;
  headline: string | null;
  bio: string | null;
  durationMinutes: number;
}

interface Slot { start_time: number; end_time: number; }

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const DAY_LABELS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];

// "YYYY-MM-DD" in local timezone
function toLocalDate(unix: number): string {
  return new Date(unix * 1000).toLocaleDateString('en-CA', { timeZone: TZ });
}

// "YYYY-MM-DD" for a Date object
function dateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function buildGrid(year: number, month: number): (Date | null)[] {
  const firstWeekday = new Date(year, month, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const grid: (Date | null)[] = Array(firstWeekday).fill(null);
  for (let d = 1; d <= daysInMonth; d++) grid.push(new Date(year, month, d));
  while (grid.length % 7 !== 0) grid.push(null);
  return grid;
}

function formatTime(unix: number, use24h: boolean): string {
  return new Date(unix * 1000).toLocaleTimeString(undefined, {
    timeZone: TZ,
    hour: 'numeric',
    minute: '2-digit',
    hour12: !use24h,
  });
}

function formatDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
}

function formatRange(start: number, end: number): string {
  const opts: Intl.DateTimeFormatOptions = { timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true };
  const s = new Date(start * 1000).toLocaleTimeString(undefined, opts);
  const e = new Date(end * 1000).toLocaleTimeString(undefined, opts);
  return `${s} – ${e}`;
}

// ── Info sidebar ────────────────────────────────────────────────────────────────

function InfoPanel({
  mentorName, headline, bio, durationMinutes, selectedSlot,
}: {
  mentorName: string; headline: string | null; bio: string | null;
  durationMinutes: number; selectedSlot: Slot | null;
}) {
  const initials = mentorName.split(' ').map((p) => p[0] ?? '').join('').slice(0, 2).toUpperCase();
  return (
    <aside className="lg:border-r border-[--color-border] lg:pr-6 flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-full bg-gradient-to-br from-brand-700 to-accent-500 flex items-center justify-center text-white text-sm font-semibold shrink-0">
          {initials}
        </div>
        <div className="min-w-0">
          <p className="text-xs text-muted font-medium">{mentorName}</p>
          {headline && <p className="text-sm text-muted truncate">{headline}</p>}
        </div>
      </div>

      <div>
        <h2 className="text-xl font-semibold text-brand-900">{durationMinutes} min session</h2>
      </div>

      {selectedSlot && (
        <div className="flex items-start gap-2 text-sm text-brand-700 font-medium">
          <CalendarCheck className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            {formatDate(toLocalDate(selectedSlot.start_time))}<br />
            {formatRange(selectedSlot.start_time, selectedSlot.end_time)}
          </span>
        </div>
      )}

      <div className="flex flex-col gap-2 text-sm text-muted">
        <div className="flex items-center gap-2"><Clock className="h-4 w-4 shrink-0" />{durationMinutes}m</div>
        <div className="flex items-center gap-2"><Video className="h-4 w-4 shrink-0" />{C.videoCall}</div>
        <div className="flex items-center gap-2"><Globe className="h-4 w-4 shrink-0" />{TZ}</div>
      </div>

      {bio && (
        <div className="hidden lg:block pt-4 border-t border-[--color-border]">
          <p className="text-xs text-muted leading-relaxed line-clamp-6">{bio}</p>
        </div>
      )}
    </aside>
  );
}

// ── Month calendar ──────────────────────────────────────────────────────────────

function CalendarPanel({
  availableDates, selectedDate, onSelect,
}: {
  availableDates: Set<string>; selectedDate: string | null; onSelect: (d: string) => void;
}) {
  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());

  useEffect(() => {
    if (availableDates.size === 0) return;
    const sorted = Array.from(availableDates).sort();
    const [y, m] = sorted[0].split('-').map(Number);
    setViewYear(y);
    setViewMonth(m - 1);
  }, [availableDates]);

  const grid = useMemo(() => buildGrid(viewYear, viewMonth), [viewYear, viewMonth]);
  const todayKey = dateKey(now);

  function prevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(v => v - 1); }
    else setViewMonth(m => m - 1);
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(v => v + 1); }
    else setViewMonth(m => m + 1);
  }

  const isPast = (d: Date) => {
    const k = dateKey(d);
    return k < todayKey;
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Month header */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-brand-900">
          <span className="font-bold">{MONTH_NAMES[viewMonth]}</span>{' '}
          <span className="font-normal text-muted">{viewYear}</span>
        </h3>
        <div className="flex gap-1">
          <button type="button" onClick={prevMonth}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-brand-50 transition-colors">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button type="button" onClick={nextMonth}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-brand-50 transition-colors">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Day labels */}
      <div className="grid grid-cols-7 text-center">
        {DAY_LABELS.map((d) => (
          <div key={d} className="text-xs font-medium text-muted py-1">{d}</div>
        ))}
      </div>

      {/* Grid */}
      <div className="grid grid-cols-7 gap-y-1">
        {grid.map((date, i) => {
          if (!date) return <div key={`empty-${i}`} />;
          const k = dateKey(date);
          const hasSlots = availableDates.has(k);
          const past = isPast(date);
          const isToday = k === todayKey;
          const selected = k === selectedDate;
          const disabled = past || !hasSlots;

          return (
            <div key={k} className="flex justify-center">
              <button
                type="button"
                disabled={disabled}
                onClick={() => onSelect(k)}
                className={cn(
                  'relative w-9 h-9 rounded-full text-sm font-medium transition-colors',
                  selected
                    ? 'bg-brand-900 text-white'
                    : hasSlots && !past
                    ? 'bg-brand-50 text-brand-900 hover:bg-brand-100 font-semibold'
                    : 'text-muted/40 cursor-not-allowed',
                )}
              >
                {date.getDate()}
                {isToday && !selected && (
                  <span className="absolute bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-brand-500" />
                )}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Time slots panel ────────────────────────────────────────────────────────────

function TimeSlotsPanel({
  selectedDate, slots, onSelect, use24h, setUse24h,
}: {
  selectedDate: string; slots: Slot[]; onSelect: (s: Slot) => void;
  use24h: boolean; setUse24h: (v: boolean) => void;
}) {
  const [y, m, d] = selectedDate.split('-').map(Number);
  const dateLabel = new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric',
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-brand-900">{dateLabel}</h3>
        <div className="flex rounded-md border border-[--color-border] overflow-hidden text-xs">
          {(['12h', '24h'] as const).map((fmt) => (
            <button
              key={fmt}
              type="button"
              onClick={() => setUse24h(fmt === '24h')}
              className={cn(
                'px-2.5 py-1.5 font-medium transition-colors',
                (fmt === '24h') === use24h
                  ? 'bg-brand-900 text-white'
                  : 'text-muted hover:text-foreground',
              )}
            >
              {fmt}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2 max-h-[420px] overflow-y-auto pr-1">
        {slots.map((slot) => (
          <button
            key={slot.start_time}
            type="button"
            onClick={() => onSelect(slot)}
            className="flex items-center gap-2.5 px-4 h-11 rounded-lg border border-[--color-border] text-sm font-medium text-foreground hover:border-brand-500 hover:bg-brand-50 transition-colors text-left"
          >
            <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
            {formatTime(slot.start_time, use24h)}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Booking form ────────────────────────────────────────────────────────────────

function BookingForm({
  slug, selectedSlot, durationMinutes, onBack, onSuccess,
}: {
  slug: string; selectedSlot: Slot; durationMinutes: number;
  onBack: () => void; onSuccess: (meetUrl: string | null, email: string) => void;
}) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const supabase = createClient();
      const { data: { user } } = await supabase.auth.getUser();
      if (user) {
        setEmail(user.email ?? '');
        const fn = user.user_metadata?.full_name || user.user_metadata?.name;
        if (typeof fn === 'string') setName(fn);
      }
    })();
  }, []);

  async function confirm() {
    if (!name.trim() || !email.trim()) return;
    setError(null);
    setSubmitting(true);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const res = await fetch(`/api/mentors/${slug}/book`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
        },
        body: JSON.stringify({
          start_time: selectedSlot.start_time,
          candidate_name: name.trim(),
          candidate_email: email.trim(),
          candidate_timezone: TZ,
          notes: notes.trim() || undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Booking failed. Please try another slot.'); return; }
      onSuccess(data.meeting_url ?? null, email.trim());
    } catch {
      setError('Could not complete the booking. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Input label="Your name *" value={name} onChange={(e) => setName(e.target.value)}
        placeholder={C.form.namePlaceholder} autoComplete="name" />
      <Input label="Email address *" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
        placeholder={C.form.emailPlaceholder} autoComplete="email" />
      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-foreground">{C.form.notesLabel}</label>
        <textarea
          rows={3}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={C.form.notesPlaceholder}
          className="px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-none placeholder:text-muted shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)] focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]"
        />
      </div>
      <p className="text-xs text-muted">
        {C.form.termsPrefix}{' '}
        <a href="/terms" className="underline hover:text-foreground">{C.form.terms}</a> and{' '}
        <a href="/privacy" className="underline hover:text-foreground">{C.form.privacy}</a>.
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button type="button" onClick={onBack}
          className="text-sm font-medium text-muted hover:text-foreground transition-colors">
          {C.form.back}
        </button>
        <Button variant="accent" onClick={confirm} loading={submitting}
          disabled={!name.trim() || !email.trim()}>
          {C.form.confirm}
        </Button>
      </div>
    </div>
  );
}

// ── Main scheduler ──────────────────────────────────────────────────────────────

export function BookingWidget({ slug, mentorName, headline, bio, durationMinutes }: Props) {
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null);
  const [step, setStep] = useState<'calendar' | 'form' | 'confirmed'>('calendar');
  const [meetUrl, setMeetUrl] = useState<string | null>(null);
  const [bookedEmail, setBookedEmail] = useState('');
  const [use24h, setUse24h] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`/api/mentors/${slug}/availability?duration=${durationMinutes}`, { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok) { setLoadError(data.detail || 'Could not load availability.'); return; }
        setSlots(data.slots ?? []);
      } catch {
        setLoadError('Could not load availability for this mentor.');
      }
    })();
  }, [slug, durationMinutes]);

  // Group slots by local date
  const slotsByDate = useMemo(() => {
    const map = new Map<string, Slot[]>();
    for (const s of slots ?? []) {
      const k = toLocalDate(s.start_time);
      (map.get(k) ?? (map.set(k, []), map.get(k)!)).push(s);
    }
    return map;
  }, [slots]);

  const availableDates = useMemo(() => new Set(slotsByDate.keys()), [slotsByDate]);
  const timeSlotsForDay = selectedDate ? (slotsByDate.get(selectedDate) ?? []) : [];

  function handleSlotSelect(slot: Slot) {
    setSelectedSlot(slot);
    setStep('form');
  }

  function handleConfirmed(url: string | null, email: string) {
    setMeetUrl(url);
    setBookedEmail(email);
    setStep('confirmed');
  }

  if (loadError) {
    return (
      <div className="rounded-2xl border border-[--color-border] p-6 text-sm text-muted">
        {loadError}
      </div>
    );
  }

  if (!slots) {
    return (
      <div className="rounded-2xl border border-[--color-border] p-6 flex items-center gap-2 text-sm text-muted">
        <Loader2 className="h-4 w-4 animate-spin" /> {C.loadingSlots}
      </div>
    );
  }

  if (step === 'confirmed') {
    return (
      <div className="rounded-2xl border border-[--color-border] overflow-hidden">
        <div className="grid lg:grid-cols-[280px_1fr]">
          <div className="p-6 bg-brand-50/40 border-b lg:border-b-0 lg:border-r border-[--color-border]">
            <InfoPanel mentorName={mentorName} headline={headline} bio={bio}
              durationMinutes={durationMinutes} selectedSlot={selectedSlot} />
          </div>
          <div className="p-6 flex flex-col gap-4 justify-center">
            <div className="flex items-center gap-2">
              <CalendarCheck className="h-6 w-6 text-emerald-500" />
              <h3 className="text-lg font-semibold text-brand-900">{C.confirmedTitle}</h3>
            </div>
            <p className="text-sm text-muted">
              {C.confirmedBody(mentorName, bookedEmail)}
            </p>
            <p className="text-xs text-muted">
              The mentor has received an invite and you&apos;ve been added to the calendar event.
              Check your email to accept the calendar invite.
            </p>
            <div className="flex flex-wrap gap-2">
              {meetUrl && (
                <a href={meetUrl} target="_blank" rel="noopener noreferrer">
                  <Button variant="accent">
                    <Video className="h-4 w-4" /> {C.joinVideoCall}
                  </Button>
                </a>
              )}
              {selectedSlot && (
                <a
                  href={gcalUrl(
                    selectedSlot.start_time,
                    selectedSlot.end_time,
                    `Session with ${mentorName}`,
                    meetUrl,
                  )}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Button variant="outline">
                    <CalendarCheck className="h-4 w-4" /> {C.addToCalendar}
                  </Button>
                </a>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-[--color-border] overflow-hidden">
      <div className={cn(
        'grid',
        step === 'form'
          ? 'lg:grid-cols-[280px_1fr]'
          : selectedDate
          ? 'lg:grid-cols-[280px_1fr_300px]'
          : 'lg:grid-cols-[280px_1fr]',
      )}>
        {/* Info panel */}
        <div className="p-6 bg-brand-50/40 border-b lg:border-b-0 lg:border-r border-[--color-border]">
          <InfoPanel mentorName={mentorName} headline={headline} bio={bio}
            durationMinutes={durationMinutes} selectedSlot={step === 'form' ? selectedSlot : null} />
        </div>

        {/* Calendar */}
        {step === 'calendar' && (
          <div className="p-6 border-b lg:border-b-0 lg:border-r border-[--color-border]">
            {slots.length === 0 ? (
              <div className="flex items-center justify-center h-full min-h-[200px]">
                <p className="text-sm text-muted text-center">
                  {C.noSlotsTitle(mentorName.split(' ')[0])}<br />
                  {C.noSlotsBody}
                </p>
              </div>
            ) : (
              <CalendarPanel
                availableDates={availableDates}
                selectedDate={selectedDate}
                onSelect={setSelectedDate}
              />
            )}
          </div>
        )}

        {/* Right panel: times or form */}
        {step === 'calendar' && selectedDate && (
          <div className="p-6">
            <TimeSlotsPanel
              selectedDate={selectedDate}
              slots={timeSlotsForDay}
              onSelect={handleSlotSelect}
              use24h={use24h}
              setUse24h={setUse24h}
            />
          </div>
        )}

        {step === 'form' && selectedSlot && (
          <div className="p-6">
            <BookingForm
              slug={slug}
              selectedSlot={selectedSlot}
              durationMinutes={durationMinutes}
              onBack={() => { setStep('calendar'); setSelectedSlot(null); }}
              onSuccess={handleConfirmed}
            />
          </div>
        )}
      </div>
    </div>
  );
}
