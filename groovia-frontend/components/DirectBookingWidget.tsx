'use client';
import { useEffect, useMemo, useState } from 'react';
import {
  CalendarCheck, ChevronLeft, ChevronRight, Clock, DollarSign,
  Globe, Loader2, MessageSquare, Video,
} from 'lucide-react';
import { Button } from './ui/Button';
import { Input } from './ui/Input';
import { createClient } from '../lib/supabase/client';
import { cn } from '../lib/utils';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Service {
  id: string;
  title: string;
  description: string | null;
  type: 'video' | 'dm';
  duration: number;
  category: string | null;
  set_price: number;
  set_currency: string;
}

interface Slot { slot_start: string; slot_end: string; }

interface Question {
  id: string;
  question_text: string;
  is_required: boolean;
  question_type: 'text' | 'multiple_choice' | 'yes_no';
}

interface MentorInfo {
  id: string;
  slug: string;
  display_name: string;
  headline: string | null;
  bio: string | null;
  photo_url: string | null;
}

type Step = 'service' | 'datetime' | 'form' | 'confirmed';

// ── Calendar helpers ───────────────────────────────────────────────────────────

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const DAY_LABELS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const MONTH_NAMES = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
];

function slotDateKey(isoStr: string): string {
  return new Date(isoStr).toLocaleDateString('en-CA', { timeZone: TZ });
}

function buildGrid(year: number, month: number): (Date | null)[] {
  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth  = new Date(year, month + 1, 0).getDate();
  const grid: (Date | null)[] = Array(firstWeekday).fill(null);
  for (let d = 1; d <= daysInMonth; d++) grid.push(new Date(year, month, d));
  while (grid.length % 7 !== 0) grid.push(null);
  return grid;
}

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function formatSlotTime(isoStr: string): string {
  return new Date(isoStr).toLocaleTimeString(undefined, {
    timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true,
  });
}

function formatSlotRange(start: string, end: string): string {
  const s = formatSlotTime(start);
  const e = formatSlotTime(end);
  return `${s} – ${e}`;
}

function formatDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
}

function formatPrice(price: number, currency: string): string {
  if (price === 0) return 'Free';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(price);
}

// ── Calendar panel ─────────────────────────────────────────────────────────────

function CalendarPanel({
  availableDates, selectedDate, onSelect,
}: {
  availableDates: Set<string>; selectedDate: string | null; onSelect: (d: string) => void;
}) {
  const now = new Date();
  const [viewYear, setViewYear]   = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());

  useEffect(() => {
    if (availableDates.size === 0) return;
    const [y, m] = Array.from(availableDates).sort()[0].split('-').map(Number);
    setViewYear(y); setViewMonth(m - 1);
  }, [availableDates]);

  const grid     = useMemo(() => buildGrid(viewYear, viewMonth), [viewYear, viewMonth]);
  const todayKey = dateKey(now);

  function prevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(v => v - 1); }
    else setViewMonth(m => m - 1);
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(v => v + 1); }
    else setViewMonth(m => m + 1);
  }

  return (
    <div className="flex flex-col gap-4">
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

      <div className="grid grid-cols-7 text-center">
        {DAY_LABELS.map(d => (
          <div key={d} className="text-xs font-medium text-muted py-1">{d}</div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-y-1">
        {grid.map((date, i) => {
          if (!date) return <div key={`e-${i}`} />;
          const k        = dateKey(date);
          const hasSlots = availableDates.has(k);
          const past     = k < todayKey;
          const selected = k === selectedDate;
          return (
            <div key={k} className="flex justify-center">
              <button
                type="button"
                disabled={past || !hasSlots}
                onClick={() => onSelect(k)}
                className={cn(
                  'relative w-9 h-9 rounded-full text-sm font-medium transition-colors',
                  selected          ? 'bg-brand-900 text-white'
                  : hasSlots && !past ? 'bg-brand-50 text-brand-900 hover:bg-brand-100 font-semibold'
                  : 'text-muted/40 cursor-not-allowed',
                )}
              >
                {date.getDate()}
                {k === todayKey && !selected && (
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

// ── Main widget ────────────────────────────────────────────────────────────────

interface Props {
  mentor: MentorInfo;
}

export function DirectBookingWidget({ mentor }: Props) {
  const [step, setStep]                   = useState<Step>('service');
  const [services, setServices]           = useState<Service[] | null>(null);
  const [servicesError, setServicesError] = useState<string | null>(null);
  const [selectedService, setSelectedService] = useState<Service | null>(null);

  const [slots, setSlots]               = useState<Slot[] | null>(null);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsError, setSlotsError]     = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null);

  const [questions, setQuestions]   = useState<Question[]>([]);
  const [answers, setAnswers]       = useState<Record<string, string>>({});
  const [name, setName]             = useState('');
  const [email, setEmail]           = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError]   = useState<string | null>(null);
  const [bookingId, setBookingId]   = useState<string | null>(null);

  // Load services
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`/api/mentors/${mentor.slug}/services`, { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok) { setServicesError('Could not load services.'); return; }
        setServices(data.services ?? []);
      } catch { setServicesError('Could not load services.'); }
    })();
  }, [mentor.slug]);

  // Pre-fill name/email from session
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

  // Load slots when a service is selected
  async function selectService(svc: Service) {
    setSelectedService(svc);
    setSlots(null);
    setSlotsError(null);
    setSelectedDate(null);
    setSelectedSlot(null);
    setSlotsLoading(true);
    setStep('datetime');
    try {
      const today = new Date();
      const maxDate = new Date(today);
      maxDate.setDate(today.getDate() + 30);
      const from = today.toLocaleDateString('en-CA');
      const to   = maxDate.toLocaleDateString('en-CA');
      const res = await fetch(
        `/api/booking/slots/${mentor.id}/${svc.id}?from_date=${from}&to_date=${to}`,
        { cache: 'no-store' },
      );
      const data = await res.json();
      if (!res.ok) { setSlotsError(data.detail || 'Could not load slots.'); return; }
      setSlots(data.slots ?? []);
    } catch { setSlotsError('Could not load available slots.'); }
    finally { setSlotsLoading(false); }
  }

  // Load questions when moving to form
  async function selectSlot(slot: Slot) {
    setSelectedSlot(slot);
    setStep('form');
    try {
      const res = await fetch(`/api/mentor/services/${selectedService!.id}/questions/public`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        setQuestions(data ?? []);
      }
    } catch { /* questions are optional */ }
  }

  // Submit booking
  async function submitBooking() {
    if (!selectedSlot || !selectedService) return;
    if (!name.trim() || !email.trim()) { setFormError('Name and email are required.'); return; }
    const missing = questions.find(q => q.is_required && !answers[q.id]?.trim());
    if (missing) { setFormError(`Please answer: "${missing.question_text}"`); return; }
    setFormError(null);
    setSubmitting(true);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const res = await fetch('/api/booking', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
        },
        body: JSON.stringify({
          mentor_id:   mentor.id,
          service_id:  selectedService.id,
          slot_time:   selectedSlot.slot_start,
          email:       email.trim(),
          name:        name.trim(),
          timezone:    TZ,
          answers:     questions.map(q => ({ question_id: q.id, answer_text: answers[q.id] ?? '' })),
        }),
      });
      const data = await res.json();
      if (!res.ok) { setFormError(data.detail || 'Booking failed. Please try another slot.'); return; }
      setBookingId(data.booking_id);
      setStep('confirmed');
    } catch { setFormError('Could not complete the booking. Please try again.'); }
    finally { setSubmitting(false); }
  }

  // Slot grouping
  const slotsByDate = useMemo(() => {
    const map = new Map<string, Slot[]>();
    for (const s of slots ?? []) {
      const k = slotDateKey(s.slot_start);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(s);
    }
    return map;
  }, [slots]);
  const availableDates   = useMemo(() => new Set(slotsByDate.keys()), [slotsByDate]);
  const timeSlotsForDay  = selectedDate ? (slotsByDate.get(selectedDate) ?? []) : [];
  const initials         = mentor.display_name.split(' ').map(p => p[0] ?? '').join('').slice(0, 2).toUpperCase();

  // ── Confirmed state ──────────────────────────────────────────────────────────
  if (step === 'confirmed') {
    return (
      <div className="rounded-2xl border border-[--color-border] overflow-hidden">
        <div className="grid lg:grid-cols-[280px_1fr]">
          <aside className="p-6 bg-brand-50/40 border-b lg:border-b-0 lg:border-r border-[--color-border] flex flex-col gap-3">
            <MentorMeta mentor={mentor} initials={initials} />
            {selectedService && <ServiceSummary svc={selectedService} />}
          </aside>
          <div className="p-6 flex flex-col gap-4 justify-center">
            <div className="flex items-center gap-2">
              <CalendarCheck className="h-6 w-6 text-emerald-500" />
              <h3 className="text-lg font-semibold text-brand-900">Booking confirmed!</h3>
            </div>
            <p className="text-sm text-muted">
              You&apos;re all set. A confirmation email has been sent to <strong>{email}</strong>.
            </p>
            {selectedSlot && (
              <div className="rounded-lg border border-brand-100 bg-brand-50 p-4 flex flex-col gap-1">
                <p className="text-sm font-semibold text-brand-900">
                  {formatDate(slotDateKey(selectedSlot.slot_start))}
                </p>
                <p className="text-sm text-muted">
                  {formatSlotRange(selectedSlot.slot_start, selectedSlot.slot_end)} — {TZ}
                </p>
                <p className="text-xs text-muted mt-1">Booking ID: <code>{bookingId}</code></p>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-[--color-border] overflow-hidden">
      <div className={cn(
        'grid',
        step === 'service'
          ? 'lg:grid-cols-[280px_1fr]'
          : step === 'datetime' && selectedDate
          ? 'lg:grid-cols-[280px_1fr_300px]'
          : 'lg:grid-cols-[280px_1fr]',
      )}>
        {/* ── Left panel ───────────────────────────────────────────── */}
        <aside className="p-6 bg-brand-50/40 border-b lg:border-b-0 lg:border-r border-[--color-border] flex flex-col gap-4">
          <MentorMeta mentor={mentor} initials={initials} />
          {selectedService && <ServiceSummary svc={selectedService} />}
          {selectedSlot && (
            <div className="flex items-start gap-2 text-sm text-brand-700 font-medium">
              <CalendarCheck className="h-4 w-4 mt-0.5 shrink-0" />
              <span>
                {formatDate(slotDateKey(selectedSlot.slot_start))}<br />
                {formatSlotRange(selectedSlot.slot_start, selectedSlot.slot_end)}
              </span>
            </div>
          )}
          <div className="flex items-center gap-2 text-sm text-muted">
            <Globe className="h-4 w-4 shrink-0" />{TZ}
          </div>
        </aside>

        {/* ── Step 1: Service selection ─────────────────────────────── */}
        {step === 'service' && (
          <div className="p-6">
            <h3 className="text-base font-semibold text-brand-900 mb-4">Choose a session type</h3>
            {servicesError ? (
              <p className="text-sm text-red-600">{servicesError}</p>
            ) : services === null ? (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading services…
              </div>
            ) : services.length === 0 ? (
              <p className="text-sm text-muted">
                {mentor.display_name.split(' ')[0]} hasn&apos;t set up sessions yet. Check back soon.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {services.map(svc => (
                  <button
                    key={svc.id}
                    type="button"
                    onClick={() => selectService(svc)}
                    className="text-left rounded-xl border border-[--color-border] p-4 hover:border-brand-400 hover:bg-brand-50/60 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex flex-col gap-1 flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {svc.type === 'video'
                            ? <Video className="h-4 w-4 text-brand-600 shrink-0" />
                            : <MessageSquare className="h-4 w-4 text-brand-600 shrink-0" />
                          }
                          <span className="text-sm font-semibold text-foreground">{svc.title}</span>
                        </div>
                        {svc.description && (
                          <p className="text-xs text-muted leading-relaxed line-clamp-2">{svc.description}</p>
                        )}
                        <div className="flex items-center gap-3 mt-1 text-xs text-muted">
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />{svc.duration}m
                          </span>
                          {svc.category && <span>{svc.category}</span>}
                        </div>
                      </div>
                      <span className="shrink-0 text-sm font-semibold text-brand-700">
                        {formatPrice(svc.set_price, svc.set_currency)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Step 2: Date + time ───────────────────────────────────── */}
        {step === 'datetime' && (
          <div className="p-6 border-b lg:border-b-0 lg:border-r border-[--color-border]">
            <button
              type="button"
              onClick={() => { setStep('service'); setSelectedService(null); }}
              className="text-xs text-muted hover:text-foreground flex items-center gap-1 mb-4"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Change service
            </button>
            {slotsError ? (
              <p className="text-sm text-red-600">{slotsError}</p>
            ) : slotsLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading availability…
              </div>
            ) : slots && slots.length === 0 ? (
              <p className="text-sm text-muted text-center mt-8">
                No open slots in the next 30 days.<br />
                <span className="text-xs">Check back later.</span>
              </p>
            ) : slots ? (
              <CalendarPanel
                availableDates={availableDates}
                selectedDate={selectedDate}
                onSelect={setSelectedDate}
              />
            ) : null}
          </div>
        )}

        {/* ── Step 2b: Time slots ───────────────────────────────────── */}
        {step === 'datetime' && selectedDate && slots && (
          <div className="p-6">
            <h3 className="text-sm font-semibold text-brand-900 mb-3">{formatDate(selectedDate)}</h3>
            <div className="flex flex-col gap-2 max-h-[380px] overflow-y-auto pr-1">
              {timeSlotsForDay.map(slot => (
                <button
                  key={slot.slot_start}
                  type="button"
                  onClick={() => selectSlot(slot)}
                  className="flex items-center gap-2.5 px-4 h-11 rounded-lg border border-[--color-border] text-sm font-medium text-foreground hover:border-brand-500 hover:bg-brand-50 transition-colors text-left"
                >
                  <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
                  {formatSlotTime(slot.slot_start)}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Step 3: Booking form ──────────────────────────────────── */}
        {step === 'form' && (
          <div className="p-6 flex flex-col gap-4">
            <button
              type="button"
              onClick={() => { setStep('datetime'); setSelectedSlot(null); }}
              className="text-xs text-muted hover:text-foreground flex items-center gap-1 self-start"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Change time
            </button>
            <h3 className="text-base font-semibold text-brand-900">Your details</h3>
            <Input label="Full name *" value={name} onChange={e => setName(e.target.value)}
              placeholder="Your name" autoComplete="name" />
            <Input label="Email *" type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="your@email.com" autoComplete="email" />
            {questions.map(q => (
              <div key={q.id} className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-foreground">
                  {q.question_text}
                  {q.is_required && <span className="text-red-500 ml-0.5">*</span>}
                </label>
                {q.question_type === 'yes_no' ? (
                  <div className="flex gap-3">
                    {['Yes', 'No'].map(opt => (
                      <label key={opt} className="flex items-center gap-1.5 text-sm cursor-pointer">
                        <input type="radio" name={q.id} value={opt}
                          checked={answers[q.id] === opt}
                          onChange={() => setAnswers(a => ({ ...a, [q.id]: opt }))} />
                        {opt}
                      </label>
                    ))}
                  </div>
                ) : (
                  <textarea
                    rows={2}
                    value={answers[q.id] ?? ''}
                    onChange={e => setAnswers(a => ({ ...a, [q.id]: e.target.value }))}
                    className="px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-none placeholder:text-muted shadow-[0_0_0_1px_rgba(15,23,42,0.06)] focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]"
                  />
                )}
              </div>
            ))}
            {formError && <p className="text-sm text-red-600">{formError}</p>}
            <Button
              variant="accent"
              onClick={submitBooking}
              loading={submitting}
              disabled={!name.trim() || !email.trim()}
            >
              Confirm booking
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function MentorMeta({ mentor, initials }: { mentor: MentorInfo; initials: string }) {
  return (
    <div className="flex items-center gap-3">
      {mentor.photo_url ? (
        <img src={mentor.photo_url} alt={mentor.display_name}
          className="h-10 w-10 rounded-full object-cover shrink-0" />
      ) : (
        <div className="h-10 w-10 rounded-full bg-gradient-to-br from-brand-700 to-accent-500 flex items-center justify-center text-white text-sm font-semibold shrink-0">
          {initials}
        </div>
      )}
      <div className="min-w-0">
        <p className="text-xs font-medium text-foreground truncate">{mentor.display_name}</p>
        {mentor.headline && <p className="text-xs text-muted truncate">{mentor.headline}</p>}
      </div>
    </div>
  );
}

function ServiceSummary({ svc }: { svc: Service }) {
  return (
    <div className="flex flex-col gap-1 text-sm text-muted">
      <p className="font-semibold text-foreground text-sm">{svc.title}</p>
      <div className="flex items-center gap-1.5">
        <Clock className="h-3.5 w-3.5 shrink-0" />{svc.duration} min
      </div>
      <div className="flex items-center gap-1.5">
        <DollarSign className="h-3.5 w-3.5 shrink-0" />{formatPrice(svc.set_price, svc.set_currency)}
      </div>
    </div>
  );
}
