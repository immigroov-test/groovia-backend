'use client';
import { useState } from 'react';
import { PHONE_CODES } from '../../lib/phoneCodes';
import { cn } from '../../lib/utils';

interface Props {
  value: string;
  onChange: (val: string) => void;
  label?: string;
  required?: boolean;
  hint?: string;
}

export function PhoneInput({ value, onChange, label = 'Phone Number', required, hint }: Props) {
  const [dialCode, setDialCode] = useState('+31');
  const [number, setNumber] = useState('');
  const [filterQuery, setFilterQuery] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const filtered = filterQuery
    ? PHONE_CODES.filter(
        (c) =>
          c.name.toLowerCase().includes(filterQuery.toLowerCase()) ||
          c.dial.includes(filterQuery),
      )
    : PHONE_CODES;

  function select(dial: string) {
    setDialCode(dial);
    setDropdownOpen(false);
    setFilterQuery('');
    onChange(`${dial} ${number}`);
  }

  function handleNumber(e: React.ChangeEvent<HTMLInputElement>) {
    const n = e.target.value.replace(/[^\d\s\-()]/g, '');
    setNumber(n);
    onChange(`${dialCode} ${n}`);
  }

  const selectedEntry = PHONE_CODES.find((c) => c.dial === dialCode);

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label className="text-sm font-medium text-foreground">
          {label}{required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
      )}
      <div className="flex gap-0 relative">
        {/* Country code picker */}
        <button
          type="button"
          onClick={() => setDropdownOpen((o) => !o)}
          className={cn(
            'flex items-center gap-1.5 px-3 h-10 rounded-l-lg border-r-0 text-sm bg-white',
            'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
            'hover:bg-brand-50 whitespace-nowrap shrink-0',
          )}
        >
          <span>{selectedEntry?.flag ?? '🌍'}</span>
          <span className="font-medium text-foreground">{dialCode}</span>
          <svg className="h-3 w-3 text-muted ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {/* Number input */}
        <input
          type="tel"
          value={number}
          onChange={handleNumber}
          placeholder="612 345 678"
          required={required}
          className={cn(
            'flex-1 h-10 px-3 rounded-r-lg text-sm bg-white text-foreground',
            'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
            'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25),0_1px_2px_rgba(15,23,42,0.04)]',
            'placeholder:text-muted',
          )}
        />

        {/* Dropdown */}
        {dropdownOpen && (
          <div className="absolute top-full left-0 z-50 mt-1 w-64 bg-white rounded-xl border border-[--color-border] shadow-xl overflow-hidden">
            <div className="p-2 border-b border-[--color-border]">
              <input
                type="text"
                autoFocus
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                placeholder="Search country…"
                className="w-full px-2 py-1.5 text-sm rounded-lg bg-brand-50 focus:outline-none placeholder:text-muted"
              />
            </div>
            <ul className="max-h-52 overflow-y-auto py-1">
              {filtered.map((c) => (
                <li
                  key={c.code}
                  onClick={() => select(c.dial)}
                  className="flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-brand-50"
                >
                  <span>{c.flag}</span>
                  <span className="flex-1 text-foreground">{c.name}</span>
                  <span className="text-muted shrink-0">{c.dial}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
