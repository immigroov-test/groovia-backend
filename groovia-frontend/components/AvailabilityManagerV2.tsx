'use client';
import { useEffect, useState } from 'react';
import { Loader2, Plus, Trash2, Ban, CalendarPlus } from 'lucide-react';
import { Button } from './ui/Button';
import { Input } from './ui/Input';
import { Card, CardBody } from './ui/Card';
import { cn } from '../lib/utils';

const DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'] as const;
type Day = typeof DAYS[number];

interface WeeklySlot { id: string; weekday: string; start_time: string; end_time: string; timezone: string; }
interface SpecificSlot { id: string; slot_date: string; start_time: string | null; end_time: string | null; timezone: string; is_booked: boolean; is_blackout: boolean; }
interface Rules { days_ahead: number; min_notice_hours: number; cancel_hours: number; timezone: string; }

async function apiFetch(path: string, method = 'GET', body?: object) {
  const supabase = (await import('../lib/supabase/client')).createClient();
  const { data: { session } } = await supabase.auth.getSession();
  const res = await fetch(path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const API = '/api/mentor/availability-v2';

export function AvailabilityManagerV2() {
  const [weekly, setWeekly]     = useState<WeeklySlot[]>([]);
  const [specific, setSpecific] = useState<SpecificSlot[]>([]);
  const [rules, setRules]       = useState<Rules | null>(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);

  const [newSlot, setNewSlot] = useState<{ day: Day; start: string; end: string }>({
    day: 'Monday', start: '09:00', end: '17:00',
  });
  const [addingSlot, setAddingSlot]       = useState(false);
  const [slotError, setSlotError]         = useState<string | null>(null);

  const [overrideDate, setOverrideDate]   = useState('');
  const [overrideStart, setOverrideStart] = useState('09:00');
  const [overrideEnd, setOverrideEnd]     = useState('17:00');
  const [blockDate, setBlockDate]         = useState('');

  const [rulesForm, setRulesForm]       = useState<Rules | null>(null);
  const [savingRules, setSavingRules]   = useState(false);
  const [rulesError, setRulesError]     = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try {
      const [w, s, r] = await Promise.all([
        apiFetch(`${API}/weekly`),
        apiFetch(`${API}/specific`),
        apiFetch(`${API}/rules`),
      ]);
      setWeekly(w ?? []);
      setSpecific(s ?? []);
      setRules(r);
      setRulesForm(r);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function addWeekly() {
    if (!newSlot.start || !newSlot.end) { setSlotError('Set start and end times.'); return; }
    if (newSlot.end <= newSlot.start) { setSlotError('End must be after start.'); return; }
    setSlotError(null); setAddingSlot(true);
    try {
      await apiFetch(`${API}/weekly`, 'POST', {
        weekday: newSlot.day, start_time: newSlot.start, end_time: newSlot.end,
      });
      await load();
    } catch (e: any) { setSlotError(e.message); }
    finally { setAddingSlot(false); }
  }

  async function deleteWeekly(id: string) {
    try {
      await apiFetch(`${API}/weekly/${id}/delete`, 'POST');
      setWeekly(w => w.filter(s => s.id !== id));
    } catch (e: any) { setError(e.message); }
  }

  async function doBlockDate() {
    if (!blockDate) return;
    try {
      await apiFetch(`${API}/block-date`, 'POST', { slot_date: blockDate });
      setBlockDate('');
      await load();
    } catch (e: any) { setError(e.message); }
  }

  async function doOverrideDate() {
    if (!overrideDate || !overrideStart || !overrideEnd) return;
    if (overrideEnd <= overrideStart) { setSlotError('End must be after start.'); return; }
    try {
      await apiFetch(`${API}/override-date`, 'POST', {
        slot_date: overrideDate, start_time: overrideStart, end_time: overrideEnd,
      });
      setOverrideDate('');
      await load();
    } catch (e: any) { setError(e.message); }
  }

  async function deleteSpecific(id: string) {
    try {
      await apiFetch(`${API}/specific/${id}/delete`, 'POST');
      setSpecific(s => s.filter(x => x.id !== id));
    } catch (e: any) { setError(e.message); }
  }

  async function saveRules() {
    if (!rulesForm) return;
    setSavingRules(true); setRulesError(null);
    try {
      await apiFetch(`${API}/rules`, 'POST', {
        days_ahead:        rulesForm.days_ahead,
        min_notice_hours:  rulesForm.min_notice_hours,
        cancel_hours:      rulesForm.cancel_hours,
      });
      setRules(rulesForm);
    } catch (e: any) { setRulesError(e.message); }
    finally { setSavingRules(false); }
  }

  if (loading) return (
    <div className="flex items-center gap-2 text-sm text-muted py-6">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading availability…
    </div>
  );
  if (error) return <p className="text-sm text-red-600">{error}</p>;

  const byDay = DAYS.reduce<Record<string, WeeklySlot[]>>((acc, d) => {
    acc[d] = weekly.filter(s => s.weekday === d);
    return acc;
  }, {} as Record<string, WeeklySlot[]>);

  return (
    <div className="flex flex-col gap-6">

      {/* ── Weekly schedule ─────────────────────────────────────── */}
      <Card>
        <CardBody className="pt-5 flex flex-col gap-4">
          <h3 className="text-sm font-semibold text-foreground">Weekly schedule</h3>
          <p className="text-xs text-muted -mt-2">Set your recurring availability. Slots repeat every week.</p>

          <div className="flex flex-col gap-2">
            {DAYS.map(day => (
              <div key={day} className="flex items-start gap-3">
                <span className="text-xs font-medium text-muted w-24 pt-1 shrink-0">{day}</span>
                <div className="flex flex-wrap gap-1.5 flex-1">
                  {byDay[day].map(slot => (
                    <div key={slot.id}
                      className="flex items-center gap-1 bg-brand-50 text-brand-800 rounded-full px-2.5 py-0.5 text-xs font-medium">
                      {slot.start_time.slice(0,5)}–{slot.end_time.slice(0,5)}
                      <button onClick={() => deleteWeekly(slot.id)}
                        className="ml-0.5 text-brand-400 hover:text-red-500 transition-colors">
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="border-t border-[--color-border] pt-4 flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-muted">Day</label>
              <select value={newSlot.day}
                onChange={e => setNewSlot(s => ({ ...s, day: e.target.value as Day }))}
                className="h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300">
                {DAYS.map(d => <option key={d}>{d}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-muted">From</label>
              <input type="time" value={newSlot.start}
                onChange={e => setNewSlot(s => ({ ...s, start: e.target.value }))}
                className="h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-muted">To</label>
              <input type="time" value={newSlot.end}
                onChange={e => setNewSlot(s => ({ ...s, end: e.target.value }))}
                className="h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
            </div>
            <Button variant="outline" onClick={addWeekly} loading={addingSlot}
              className="h-9 flex items-center gap-1.5">
              <Plus className="h-4 w-4" /> Add
            </Button>
          </div>
          {slotError && <p className="text-xs text-red-600">{slotError}</p>}
        </CardBody>
      </Card>

      {/* ── Date overrides ──────────────────────────────────────── */}
      <Card>
        <CardBody className="pt-5 flex flex-col gap-4">
          <h3 className="text-sm font-semibold text-foreground">Date overrides</h3>
          <p className="text-xs text-muted -mt-2">Block a date (no sessions) or add special hours for a specific day.</p>

          <div className="grid sm:grid-cols-2 gap-4">
            {/* Block a date */}
            <div className="flex flex-col gap-2 rounded-xl border border-[--color-border] p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted">
                <Ban className="h-3.5 w-3.5" /> Block a date
              </div>
              <input type="date" value={blockDate}
                onChange={e => setBlockDate(e.target.value)}
                min={new Date().toLocaleDateString('en-CA')}
                className="h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
              <Button variant="outline" onClick={doBlockDate} disabled={!blockDate}
                className="h-9 text-xs">
                Block this date
              </Button>
            </div>

            {/* Override date hours */}
            <div className="flex flex-col gap-2 rounded-xl border border-[--color-border] p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted">
                <CalendarPlus className="h-3.5 w-3.5" /> Override hours for a date
              </div>
              <input type="date" value={overrideDate}
                onChange={e => setOverrideDate(e.target.value)}
                min={new Date().toLocaleDateString('en-CA')}
                className="h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
              <div className="flex gap-2">
                <input type="time" value={overrideStart}
                  onChange={e => setOverrideStart(e.target.value)}
                  className="flex-1 h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none" />
                <span className="text-muted pt-2 text-xs">–</span>
                <input type="time" value={overrideEnd}
                  onChange={e => setOverrideEnd(e.target.value)}
                  className="flex-1 h-9 px-2 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none" />
              </div>
              <Button variant="outline" onClick={doOverrideDate} disabled={!overrideDate}
                className="h-9 text-xs">
                Set hours
              </Button>
            </div>
          </div>

          {/* Existing specific entries */}
          {specific.length > 0 && (
            <div className="flex flex-col gap-1.5 mt-1">
              <p className="text-xs font-medium text-muted">Upcoming overrides & blocks</p>
              {specific.map(s => (
                <div key={s.id} className={cn(
                  'flex items-center justify-between rounded-lg px-3 py-2 text-xs',
                  s.is_blackout ? 'bg-red-50 text-red-800' : 'bg-brand-50 text-brand-800',
                )}>
                  <span>
                    {s.slot_date}
                    {s.is_blackout ? ' — blocked' : ` · ${s.start_time?.slice(0,5)}–${s.end_time?.slice(0,5)}`}
                    {s.is_booked ? ' (booked)' : ''}
                  </span>
                  <button onClick={() => deleteSpecific(s.id)} disabled={s.is_booked}
                    className="ml-2 text-current opacity-60 hover:opacity-100 transition-opacity disabled:cursor-not-allowed">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      {/* ── Booking rules ────────────────────────────────────────── */}
      {rulesForm && (
        <Card>
          <CardBody className="pt-5 flex flex-col gap-4">
            <h3 className="text-sm font-semibold text-foreground">Booking rules</h3>
            <div className="grid sm:grid-cols-3 gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-foreground">Booking window (days)</label>
                <input type="number" min={1} max={365} value={rulesForm.days_ahead}
                  onChange={e => setRulesForm(r => r ? { ...r, days_ahead: parseInt(e.target.value) || 30 } : r)}
                  className="h-10 px-3 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
                <p className="text-xs text-muted">How far ahead mentees can book</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-foreground">Minimum notice (hrs)</label>
                <input type="number" min={0} max={168} step={0.5} value={rulesForm.min_notice_hours}
                  onChange={e => setRulesForm(r => r ? { ...r, min_notice_hours: parseFloat(e.target.value) || 0 } : r)}
                  className="h-10 px-3 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
                <p className="text-xs text-muted">Minimum lead time before a slot can be booked</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-foreground">Cancellation window (hrs)</label>
                <input type="number" min={1} max={168} value={rulesForm.cancel_hours}
                  onChange={e => setRulesForm(r => r ? { ...r, cancel_hours: parseInt(e.target.value) || 24 } : r)}
                  className="h-10 px-3 rounded-lg border border-[--color-border] text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-300" />
                <p className="text-xs text-muted">How many hours before the session cancellation is allowed</p>
              </div>
            </div>
            {rulesError && <p className="text-sm text-red-600">{rulesError}</p>}
            <div className="flex items-center gap-2">
              <Button variant="accent" onClick={saveRules} loading={savingRules}>Save rules</Button>
              <span className="text-xs text-muted">Timezone: {rulesForm.timezone}</span>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
