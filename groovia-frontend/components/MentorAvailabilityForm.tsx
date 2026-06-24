'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '../lib/supabase/client';
import { Card, CardBody } from './ui/Card';
import { Button } from './ui/Button';
import { WeeklyAvailabilityGrid, type AvailabilitySlot } from './WeeklyAvailabilityGrid';
import { cn } from '../lib/utils';

const DURATION_OPTIONS = [
  { minutes: 30, label: '30 min' },
  { minutes: 60, label: '60 min' },
  { minutes: 90, label: '90 min' },
];

interface Props {
  initialSlots: AvailabilitySlot[];
  initialDuration: number;
}

export function MentorAvailabilityForm({ initialSlots, initialDuration }: Props) {
  const router = useRouter();
  const [slots, setSlots] = useState<AvailabilitySlot[]>(initialSlots);
  const [duration, setDuration] = useState(initialDuration);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) { setError('Session expired. Please sign in again.'); return; }
      const res = await fetch('/api/mentor/availability', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({
          slots,
          session_duration_minutes: duration,
          availability_type: 'manual',
        }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Save failed.'); return; }
      setSaved(true);
      router.refresh();
    } catch {
      setError('Save failed. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-6">
      <Card>
        <CardBody className="pt-6 flex flex-col gap-6">
          <div>
            <h2 className="text-base font-semibold text-foreground">Weekly availability</h2>
            <p className="text-sm text-muted mt-0.5">Click blocks to mark when you&apos;re available each week.</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">Session duration</span>
            <div className="flex gap-2">
              {DURATION_OPTIONS.map(({ minutes, label }) => (
                <button
                  key={minutes}
                  type="button"
                  onClick={() => setDuration(minutes)}
                  className={cn(
                    'flex-1 h-10 rounded-lg border text-sm font-medium transition-colors',
                    duration === minutes
                      ? 'bg-brand-600 border-brand-600 text-white'
                      : 'border-[--color-border] text-muted hover:text-foreground hover:border-brand-300',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <WeeklyAvailabilityGrid
            value={slots}
            onChange={setSlots}
            sessionDurationMinutes={duration}
          />

          {error && <p className="text-sm text-red-600">{error}</p>}
          {saved && <p className="text-sm text-green-600">Availability saved.</p>}

          <div>
            <Button type="submit" loading={saving}>Save availability</Button>
          </div>
        </CardBody>
      </Card>

      <div className="text-center">
        <Link href="/mentor" className="text-sm text-muted hover:text-foreground underline underline-offset-2">
          ← Back to mentor hub
        </Link>
      </div>
    </form>
  );
}
