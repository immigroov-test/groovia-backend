'use client';
import { useEffect, useRef, useState } from 'react';
import { COUNTRIES, countryFlag } from '../../lib/countries';
import { cn } from '../../lib/utils';

interface Props {
  value: string;
  onChange: (code: string) => void;
  label?: string;
  required?: boolean;
  hint?: string;
  placeholder?: string;
}

export function CountrySelect({
  value,
  onChange,
  label,
  required,
  hint,
  placeholder = 'Select country…',
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  const filtered = query
    ? COUNTRIES.filter((c) => c.name.toLowerCase().includes(query.toLowerCase()) || c.code.toLowerCase().includes(query.toLowerCase()))
    : COUNTRIES;

  const selected = COUNTRIES.find((c) => c.code === value);

  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    }
    if (open) document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [open]);

  function select(code: string) {
    onChange(code);
    setOpen(false);
    setQuery('');
  }

  return (
    <div className="flex flex-col gap-1.5" ref={containerRef}>
      {label && (
        <label className="text-sm font-medium text-foreground">
          {label}{required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
      )}

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex items-center gap-2 h-10 px-3 rounded-lg bg-white text-sm text-left',
          'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
          'hover:shadow-[0_0_0_1px_rgba(15,23,42,0.12)] focus:outline-none',
          open && 'shadow-[0_0_0_2px_rgba(29,78,216,0.25)]',
        )}
      >
        {selected ? (
          <>
            <span className="text-base leading-none">{countryFlag(selected.code)}</span>
            <span className="flex-1 text-foreground">{selected.name}</span>
          </>
        ) : (
          <span className="flex-1 text-muted">{placeholder}</span>
        )}
        <svg className="h-3.5 w-3.5 text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d={open ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'} />
        </svg>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full max-w-xs bg-white rounded-xl border border-[--color-border] shadow-xl overflow-hidden" style={{ position: 'relative' }}>
          <div className="p-2 border-b border-[--color-border]">
            <input
              type="text"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search country…"
              className="w-full px-2 py-1.5 text-sm rounded-lg bg-brand-50 focus:outline-none placeholder:text-muted"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto py-1">
            {filtered.length === 0 && (
              <li className="px-3 py-2 text-sm text-muted">No results</li>
            )}
            {filtered.map((c) => (
              <li
                key={c.code}
                onClick={() => select(c.code)}
                className={cn(
                  'flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-brand-50',
                  c.code === value && 'bg-brand-50 font-medium text-brand-700',
                )}
              >
                <span className="text-base leading-none">{countryFlag(c.code)}</span>
                <span className="flex-1 text-foreground">{c.name}</span>
                <span className="text-xs text-muted shrink-0">{c.code}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
