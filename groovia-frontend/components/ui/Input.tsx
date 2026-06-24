'use client';
import { forwardRef, useId, type InputHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { className, label, hint, error, id, ...rest },
  ref,
) {
  // useId() generates a stable id on both server and client, so SSR + hydration line up.
  // (Previously we used Math.random(), which drifted between server and browser → hydration warning.)
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium text-foreground">
          {label}
        </label>
      )}
      <input
        id={inputId}
        ref={ref}
        className={cn(
          'h-10 px-3 rounded-lg bg-white text-sm text-foreground',
          'placeholder:text-muted',
          'shadow-[0_0_0_1px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)]',
          'focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25),0_1px_2px_rgba(15,23,42,0.04)]',
          error && 'shadow-[0_0_0_1px_rgba(220,38,38,0.4)] focus:shadow-[0_0_0_2px_rgba(220,38,38,0.3)]',
          className,
        )}
        {...rest}
      />
      {error ? (
        <p className="text-xs text-red-600">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted">{hint}</p>
      ) : null}
    </div>
  );
});
