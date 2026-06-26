'use client';
import { useCallback, useEffect, useState } from 'react';
import { Loader2, CalendarClock, Video, AlertTriangle } from 'lucide-react';
import { createClient } from '../lib/supabase/client';
import { Button } from './ui/Button';
import { Badge } from './ui/Badge';
import { cn } from '../lib/utils';

// One row from my_bookings / mentor_sessions (shared shape; some fields role-specific).
export interface ManagedBooking {
  id: string;
  status: string;
  slot_time: string | null;
  slot_end: string | null;
  meeting_url: string | null;
  service_title: string | null;
  service_duration: number | null;
  other_name: string | null;
  other_email?: string | null;
  deadline_state: 'free' | 'late' | 'buffer' | null;
  reschedule_count: number;
  no_show_by: string | null;
  offer_id: string | null;
  offer_by: string | null;
  offer_status: string | null;
  range_start: string | null;
  range_end: string | null;
  selected_time: string | null;
  req_id: string | null;
  req_kind: string | null;
  req_initiated_by: string | null;
  req_status: string | null;
  req_respond_by: string | null;
}

type Role = 'mentee' | 'mentor';

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

const STATUS_TONE: Record<string, 'brand' | 'accent' | 'neutral'> = {
  confirmed: 'brand', rescheduled: 'accent', completed: 'neutral',
  cancelled: 'neutral', no_show: 'neutral', pending: 'neutral',
};

export function BookingManager({ role }: { role: Role }) {
  const [bookings, setBookings] = useState<ManagedBooking[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const endpoint = role === 'mentee' ? '/api/booking/my' : '/api/mentor/availability-v2/sessions';

  const authedFetch = useCallback(async (url: string, init?: RequestInit) => {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    return fetch(url, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await authedFetch(endpoint);
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Could not load your bookings.'); return; }
      // mentor sessions endpoint returns an array; /booking/my returns { bookings }.
      setBookings(Array.isArray(data) ? data : (data.bookings ?? []));
    } catch { setError('Could not load your bookings.'); }
  }, [authedFetch, endpoint]);

  useEffect(() => { load(); }, [load]);

  // Run an action, then refresh. `body` posted as JSON to `url`.
  async function act(id: string, url: string, body: Record<string, unknown>) {
    setBusyId(id);
    setActionError(null);
    try {
      const res = await authedFetch(url, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setActionError(data.detail || 'Action failed. Please try again.'); return; }
      await load();
    } catch { setActionError('Action failed. Please try again.'); }
    finally { setBusyId(null); }
  }

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (bookings === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading your bookings…
      </div>
    );
  }
  if (bookings.length === 0) {
    return <p className="text-sm text-muted">No bookings yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {actionError && (
        <p className="text-sm text-red-600 flex items-center gap-1.5">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {actionError}
        </p>
      )}
      {bookings.map((b) => (
        <BookingCard
          key={b.id}
          b={b}
          role={role}
          busy={busyId === b.id}
          act={act}
        />
      ))}
    </div>
  );
}

function BookingCard({
  b, role, busy, act,
}: {
  b: ManagedBooking;
  role: Role;
  busy: boolean;
  act: (id: string, url: string, body: Record<string, unknown>) => void;
}) {
  const [newTime, setNewTime] = useState('');           // datetime-local for reschedule/accept
  const [rangeStart, setRangeStart] = useState('');     // mentor propose
  const [rangeEnd, setRangeEnd] = useState('');

  const isPast = b.slot_time ? new Date(b.slot_time).getTime() < Date.now() : false;
  const afterGrace = b.slot_time ? Date.now() > new Date(b.slot_time).getTime() + 10 * 60_000 : false;
  const active = b.status === 'confirmed' || b.status === 'rescheduled';
  const toIso = (local: string) => new Date(local).toISOString();

  // A pending request the mentor must answer (mentor view) / the mentee is waiting on.
  const pendingReq = b.req_id && b.req_status === 'pending';
  // A live mentor proposal the mentee can respond to (mentee view).
  const mentorProposal = b.offer_id && b.offer_by === 'mentor' && b.offer_status === 'pending';

  return (
    <div className="rounded-xl border border-[--color-border] p-4 sm:p-5 flex flex-col gap-3">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-foreground">
              {b.service_title ?? 'Session'}
            </h3>
            <Badge tone={STATUS_TONE[b.status] ?? 'neutral'}>{b.status.replace('_', ' ')}</Badge>
            {b.reschedule_count > 0 && (
              <span className="text-xs text-muted">· rescheduled {b.reschedule_count}×</span>
            )}
          </div>
          <p className="text-sm text-muted mt-1 flex items-center gap-1.5">
            <CalendarClock className="h-3.5 w-3.5 shrink-0" /> {fmt(b.slot_time)} ({TZ})
          </p>
          <p className="text-xs text-muted mt-0.5">
            {role === 'mentee' ? 'with ' : ''}{b.other_name ?? 'your ' + (role === 'mentee' ? 'mentor' : 'mentee')}
          </p>
        </div>
        {b.meeting_url && active && (
          <a href={b.meeting_url} target="_blank" rel="noreferrer"
            className="text-sm font-medium text-brand-700 hover:underline flex items-center gap-1 shrink-0">
            <Video className="h-4 w-4" /> Join
          </a>
        )}
      </div>

      {/* ── Mentor: a pending cancel/reschedule request to answer ─────────── */}
      {role === 'mentor' && pendingReq && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 flex flex-col gap-2">
          <p className="text-sm text-amber-900">
            The attendee requested a <strong>{b.req_kind}</strong>. Respond before {fmt(b.req_respond_by)} or it auto-approves.
          </p>
          <div className="flex gap-2">
            <Button variant="primary" loading={busy}
              onClick={() => act(b.id, '/api/booking/request/respond', { request_id: b.req_id, accept: true })}>
              Approve
            </Button>
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/request/respond', { request_id: b.req_id, accept: false })}>
              Decline
            </Button>
          </div>
        </div>
      )}

      {/* ── Mentee: mentor proposed a new time ───────────────────────────── */}
      {role === 'mentee' && mentorProposal && (
        <div className="rounded-lg bg-violet-50 border border-violet-200 p-3 flex flex-col gap-2">
          <p className="text-sm text-violet-900">
            Your mentor proposed a new time between <strong>{fmt(b.range_start)}</strong> and <strong>{fmt(b.range_end)}</strong>.
            Pick a slot inside that range:
          </p>
          <input type="datetime-local" value={newTime} onChange={(e) => setNewTime(e.target.value)}
            className="h-10 px-3 rounded-lg bg-white text-sm shadow-[0_0_0_1px_rgba(15,23,42,0.1)] focus:outline-none" />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={busy} disabled={!newTime}
              onClick={() => act(b.id, '/api/booking/reschedule/accept', { offer_id: b.offer_id, slot_time: toIso(newTime) })}>
              Accept this time
            </Button>
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/reschedule/reject', { offer_id: b.offer_id })}>
              Reject
            </Button>
          </div>
        </div>
      )}

      {/* ── No-show resolution ───────────────────────────────────────────── */}
      {b.status === 'no_show' && role === 'mentee' && b.no_show_by === 'mentor' && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-3 flex flex-col gap-2">
          <p className="text-sm text-red-900">Your mentor didn&apos;t show up. How would you like to resolve it?</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/resolve-mentor', { booking_id: b.id, choice: 'rebook_same' })}>
              Rebook same mentor
            </Button>
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/resolve-mentor', { booking_id: b.id, choice: 'rebook_different' })}>
              Try a different mentor
            </Button>
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/resolve-mentor', { booking_id: b.id, choice: 'refund' })}>
              Request refund
            </Button>
          </div>
        </div>
      )}
      {b.status === 'no_show' && role === 'mentor' && b.no_show_by === 'user' && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-3 flex flex-col gap-2">
          <p className="text-sm text-red-900">The attendee was marked as a no-show.</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/resolve-customer', { booking_id: b.id, choice: 'accept_rebook' })}>
              Offer a rebook
            </Button>
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/resolve-customer', { booking_id: b.id, choice: 'reject' })}>
              Close session
            </Button>
          </div>
        </div>
      )}

      {/* ── Active-session actions ───────────────────────────────────────── */}
      {active && !mentorProposal && !pendingReq && (
        <div className="flex flex-col gap-2 border-t border-[--color-border] pt-3">
          {b.deadline_state === 'buffer' && !isPast && (
            <p className="text-xs text-muted">
              Within 2 hours of the session — changes are locked. Contact the other party directly.
            </p>
          )}

          {/* Report no-show once the session start + 10 min has passed */}
          {afterGrace && (
            <Button variant="outline" loading={busy}
              onClick={() => act(b.id, '/api/booking/no-show/flag',
                { booking_id: b.id, no_show_party: role === 'mentee' ? 'mentor' : 'user' })}>
              Report a no-show
            </Button>
          )}

          {/* Future session, not in buffer → reschedule + cancel */}
          {!isPast && b.deadline_state !== 'buffer' && (
            <div className="flex flex-col gap-3">
              {role === 'mentor' ? (
                <div className="flex flex-col gap-2">
                  <p className="text-xs font-medium text-foreground">Propose a new time range</p>
                  <div className="grid sm:grid-cols-2 gap-2">
                    <input type="datetime-local" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)}
                      className="h-10 px-3 rounded-lg bg-white text-sm shadow-[0_0_0_1px_rgba(15,23,42,0.1)] focus:outline-none" />
                    <input type="datetime-local" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)}
                      className="h-10 px-3 rounded-lg bg-white text-sm shadow-[0_0_0_1px_rgba(15,23,42,0.1)] focus:outline-none" />
                  </div>
                  <Button variant="outline" loading={busy} disabled={!rangeStart || !rangeEnd}
                    onClick={() => act(b.id, '/api/booking/reschedule/propose', {
                      booking_id: b.id,
                      offer_date: rangeStart.slice(0, 10),
                      range_start: toIso(rangeStart),
                      range_end: toIso(rangeEnd),
                    })}>
                    Send proposal
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <p className="text-xs font-medium text-foreground">
                    {b.deadline_state === 'free' ? 'Reschedule to a new time' : 'Request a reschedule'}
                  </p>
                  {b.deadline_state === 'free' ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <input type="datetime-local" value={newTime} onChange={(e) => setNewTime(e.target.value)}
                        className="h-10 px-3 rounded-lg bg-white text-sm shadow-[0_0_0_1px_rgba(15,23,42,0.1)] focus:outline-none" />
                      <Button variant="outline" loading={busy} disabled={!newTime}
                        onClick={() => act(b.id, '/api/booking/reschedule/customer', { booking_id: b.id, slot_time: toIso(newTime) })}>
                        Reschedule
                      </Button>
                    </div>
                  ) : (
                    <Button variant="outline" loading={busy}
                      onClick={() => act(b.id, '/api/booking/reschedule/request', { booking_id: b.id })}>
                      Ask mentor to approve a reschedule
                    </Button>
                  )}
                </div>
              )}

              <Button
                variant="ghost"
                loading={busy}
                className="text-red-600 hover:bg-red-50 self-start"
                onClick={() => act(b.id, '/api/booking/cancel',
                  { booking_id: b.id, cancelled_by: role === 'mentor' ? 'mentor' : 'user' })}
              >
                {b.deadline_state === 'free' || role === 'mentor' ? 'Cancel session' : 'Request cancellation'}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
