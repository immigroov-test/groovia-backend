import { type HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

type Tone = 'brand' | 'accent' | 'neutral' | 'success' | 'warning';

interface Props extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

const toneClasses: Record<Tone, string> = {
  brand:   'bg-brand-50 text-brand-900 border-brand-200',
  accent:  'bg-accent-50 text-accent-700 border-accent-200',
  neutral: 'bg-slate-100 text-slate-700 border-slate-200',
  success: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  warning: 'bg-amber-50 text-amber-700 border-amber-200',
};

export function Badge({ className, tone = 'neutral', ...rest }: Props) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full border',
        toneClasses[tone],
        className,
      )}
      {...rest}
    />
  );
}
