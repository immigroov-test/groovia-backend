'use client';
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

type Variant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'accent';
type Size = 'sm' | 'md' | 'lg';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary:
    'bg-brand-900 text-white hover:bg-brand-800 active:bg-brand-900 shadow-sm',
  secondary:
    'bg-brand-50 text-brand-900 hover:bg-brand-100 active:bg-brand-200',
  outline:
    'border border-[--color-border] bg-white text-foreground hover:border-brand-300 hover:bg-brand-50/40',
  ghost:
    'text-foreground hover:bg-brand-50/60 active:bg-brand-100',
  accent:
    'bg-accent-500 text-white hover:bg-accent-600 active:bg-accent-700 shadow-sm',
};

const sizeClasses: Record<Size, string> = {
  sm: 'h-9 px-3 text-sm rounded-md',
  md: 'h-10 px-4 text-sm rounded-lg',
  lg: 'h-12 px-6 text-base rounded-lg',
};

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { className, variant = 'primary', size = 'md', loading, disabled, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 font-medium select-none',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'active:scale-[0.98]',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <span className="inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin" />
      )}
      {children}
    </button>
  );
});
