import Image from 'next/image';
import Link from 'next/link';
import { cn } from '../../lib/utils';

interface Props {
  href?: string;
  size?: 'sm' | 'md';
  className?: string;
}

export function Logo({ href = '/', size = 'md', className }: Props) {
  // Intrinsic dimensions for Next.js Image (drives aspect ratio). The actual
  // rendered size is set via inline style + className.
  const intrinsicWidth = 280;
  const intrinsicHeight = 60;
  const renderedHeight = size === 'sm' ? 22 : 30;

  return (
    <Link
      href={href}
      className={cn('inline-flex items-center gap-2 select-none', className)}
      aria-label="Immigroov home"
    >
      <Image
        src="/Immigroov_Transparent_Logo.png"
        alt="Immigroov"
        width={intrinsicWidth}
        height={intrinsicHeight}
        priority
        className="object-contain"
        style={{ height: `${renderedHeight}px`, width: 'auto' }}
      />
    </Link>
  );
}
