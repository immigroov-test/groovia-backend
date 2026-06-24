'use client';
import { useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown, X } from 'lucide-react';
import { cn } from '../../lib/utils';

export interface SelectOption {
  value: string;
  label: string;
}

interface Props {
  label?: string;
  options: SelectOption[];
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  error?: string;
  hint?: string;
  maxSelected?: number;
}

export function MultiSelect({
  label,
  options,
  value,
  onChange,
  placeholder = 'Select…',
  error,
  hint,
  maxSelected,
}: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onOutsideClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    }
    document.addEventListener('mousedown', onOutsideClick);
    return () => document.removeEventListener('mousedown', onOutsideClick);
  }, []);

  function toggle(v: string) {
    if (value.includes(v)) {
      onChange(value.filter((x) => x !== v));
    } else {
      if (maxSelected && value.length >= maxSelected) return;
      onChange([...value, v]);
    }
  }

  function remove(v: string, e: React.MouseEvent) {
    e.stopPropagation();
    onChange(value.filter((x) => x !== v));
  }

  const filtered = query
    ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : options;

  const selectedLabels = value.map((v) => options.find((o) => o.value === v)?.label ?? v);

  return (
    <div ref={containerRef} className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
      )}

      <div
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={`${id}-listbox`}
        onClick={() => { setOpen((o) => !o); if (!open) setTimeout(() => inputRef.current?.focus(), 0); }}
        className={cn(
          'min-h-10 px-3 py-1.5 rounded-lg bg-white text-sm text-foreground cursor-pointer',
          'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
          open && 'shadow-[0_0_0_2px_rgba(29,78,216,0.25),0_1px_2px_rgba(15,23,42,0.04)]',
          error && 'shadow-[0_0_0_1px_rgba(220,38,38,0.4)]',
          'flex flex-wrap gap-1.5 items-center',
        )}
      >
        {selectedLabels.length === 0 && !open && (
          <span className="text-muted">{placeholder}</span>
        )}

        {selectedLabels.map((lbl, i) => (
          <span
            key={value[i]}
            className="inline-flex items-center gap-1 px-2 h-6 rounded-md bg-brand-50 text-brand-800 text-xs font-medium"
          >
            {lbl}
            <button
              type="button"
              onClick={(e) => remove(value[i], e)}
              aria-label={`Remove ${lbl}`}
              className="text-brand-400 hover:text-brand-700"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}

        {open && (
          <input
            ref={inputRef}
            id={id}
            type="text"
            className="flex-1 min-w-[6rem] outline-none bg-transparent text-sm placeholder:text-muted"
            placeholder="Type to filter…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClick={(e) => e.stopPropagation()}
          />
        )}

        <ChevronDown
          className={cn(
            'ml-auto h-4 w-4 text-muted shrink-0 transition-transform duration-150',
            open && 'rotate-180',
          )}
        />
      </div>

      {open && (
        <ul
          id={`${id}-listbox`}
          role="listbox"
          aria-multiselectable="true"
          className="z-30 mt-1 max-h-56 overflow-y-auto rounded-xl bg-white border border-[--color-border] shadow-lg py-1"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted">No results</li>
          ) : (
            filtered.map((opt) => {
              const selected = value.includes(opt.value);
              const disabled = !selected && !!maxSelected && value.length >= maxSelected;
              return (
                <li
                  key={opt.value}
                  role="option"
                  aria-selected={selected}
                  onClick={() => !disabled && toggle(opt.value)}
                  className={cn(
                    'flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer select-none',
                    selected ? 'text-brand-900 bg-brand-50' : 'text-foreground',
                    disabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-brand-50',
                  )}
                >
                  <span
                    className={cn(
                      'flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                      selected ? 'bg-brand-600 border-brand-600' : 'border-[--color-border]',
                    )}
                  >
                    {selected && <Check className="h-3 w-3 text-white" strokeWidth={3} />}
                  </span>
                  {opt.label}
                </li>
              );
            })
          )}
        </ul>
      )}

      {error ? (
        <p className="text-xs text-red-600">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted">{hint}</p>
      ) : null}
    </div>
  );
}
