'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';

export interface AvailabilitySlot {
  day_of_week: number;
  start_time: string;
  end_time: string;
}

interface Props {
  value: AvailabilitySlot[];
  onChange: (slots: AvailabilitySlot[]) => void;
  sessionDurationMinutes?: number;
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const START_HOUR = 7;
const END_HOUR = 21;

function makeBlocks() {
  const blocks: string[] = [];
  for (let h = START_HOUR; h < END_HOUR; h++) {
    blocks.push(`${String(h).padStart(2, '0')}:00`);
    blocks.push(`${String(h).padStart(2, '0')}:30`);
  }
  return blocks;
}

const BLOCKS = makeBlocks();

function endTime(start: string): string {
  const [h, m] = start.split(':').map(Number);
  const total = h * 60 + m + 30;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function slotKey(day: number, start: string) {
  return `${day}:${start}`;
}

function slotsToSet(slots: AvailabilitySlot[]): Set<string> {
  return new Set(slots.map((s) => slotKey(s.day_of_week, s.start_time)));
}

function setToSlots(keys: Set<string>): AvailabilitySlot[] {
  return Array.from(keys)
    .map((k) => {
      const [d, start] = k.split(':');
      return { day_of_week: Number(d), start_time: `${d.split(':')[0] ? start : start}`, end_time: endTime(start) };
    })
    .sort((a, b) => a.day_of_week - b.day_of_week || a.start_time.localeCompare(b.start_time));
}

function parseKey(key: string): [number, string] {
  const idx = key.indexOf(':');
  const day = Number(key.slice(0, idx));
  const start = key.slice(idx + 1);
  return [day, start];
}

export function WeeklyAvailabilityGrid({ value, onChange }: Props) {
  const selected = slotsToSet(value);
  const dragging = useRef<{ mode: 'add' | 'remove'; anchor: string } | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  const commit = useCallback(
    (next: Set<string>) => {
      const slots = Array.from(next)
        .map((k) => {
          const [day, start] = parseKey(k);
          return { day_of_week: day, start_time: start, end_time: endTime(start) };
        })
        .sort((a, b) => a.day_of_week - b.day_of_week || a.start_time.localeCompare(b.start_time));
      onChange(slots);
    },
    [onChange],
  );

  useEffect(() => {
    function onUp() {
      dragging.current = null;
    }
    window.addEventListener('mouseup', onUp);
    return () => window.removeEventListener('mouseup', onUp);
  }, []);

  function onCellDown(key: string) {
    const mode: 'add' | 'remove' = selected.has(key) ? 'remove' : 'add';
    dragging.current = { mode, anchor: key };
    const next = new Set(selected);
    mode === 'add' ? next.add(key) : next.delete(key);
    commit(next);
  }

  function onCellEnter(key: string) {
    setHovered(key);
    if (!dragging.current) return;
    const next = new Set(selected);
    dragging.current.mode === 'add' ? next.add(key) : next.delete(key);
    commit(next);
  }

  const selectedCount = selected.size;

  return (
    <div className="select-none">
      <p className="text-xs text-muted mb-3">
        Click or drag to toggle 30-min blocks.{' '}
        <span className="font-medium text-foreground">{selectedCount} block{selectedCount !== 1 ? 's' : ''} selected.</span>
      </p>

      <div className="overflow-x-auto">
        <div className="inline-grid min-w-[520px]" style={{ gridTemplateColumns: `4rem repeat(7, 1fr)` }}>
          {/* Header row */}
          <div />
          {DAYS.map((d) => (
            <div key={d} className="text-center text-xs font-semibold text-muted py-1.5">
              {d}
            </div>
          ))}

          {/* Time rows */}
          {BLOCKS.map((start, rowIdx) => (
            <>
              <div
                key={`label-${start}`}
                className={cn(
                  'pr-2 text-right text-[11px] text-muted leading-none',
                  rowIdx % 2 === 0 ? 'pt-1' : 'pt-1 text-transparent',
                )}
              >
                {rowIdx % 2 === 0
                  ? start
                  : '·'}
              </div>
              {DAYS.map((_, dayIdx) => {
                const key = slotKey(dayIdx, start);
                const isSelected = selected.has(key);
                const isHovered = hovered === key;
                return (
                  <div
                    key={key}
                    onMouseDown={() => onCellDown(key)}
                    onMouseEnter={() => onCellEnter(key)}
                    onMouseLeave={() => setHovered(null)}
                    className={cn(
                      'h-4 border-b border-r border-[--color-border] cursor-pointer transition-colors',
                      dayIdx === 0 && 'border-l',
                      rowIdx === 0 && 'border-t rounded-tl-sm rounded-tr-sm',
                      isSelected
                        ? 'bg-brand-500 hover:bg-brand-400'
                        : isHovered
                        ? 'bg-brand-100'
                        : 'bg-white hover:bg-brand-50',
                    )}
                  />
                );
              })}
            </>
          ))}
        </div>
      </div>

      {selectedCount > 0 && (
        <button
          type="button"
          onClick={() => commit(new Set())}
          className="mt-3 text-xs text-muted hover:text-red-500 underline underline-offset-2"
        >
          Clear all
        </button>
      )}
    </div>
  );
}
