'use client';
import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname, useRouter } from 'next/navigation';
import {
  Users, UserCircle, MessagesSquare,
  CalendarCheck, ShieldCheck, Menu, X, LogIn, LogOut,
} from 'lucide-react';
import { UI_CONTENT } from '../lib/content';
import { clearLocalChat } from '../lib/chatStorage';
import { createClient } from '../lib/supabase/client';
import { cn } from '../lib/utils';

interface Props {
  authed: boolean;
  email?: string | null;
  role?: string | null;
}

export function MobileNav({ authed, email, role }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  function hrefFor(realHref: string, gated: boolean): string {
    if (!gated || authed) return realHref;
    return `${pathname}?auth=open&next=${encodeURIComponent(realHref)}`;
  }

  async function handleSignOut() {
    setSigningOut(true);
    const supabase = createClient();
    await supabase.auth.signOut();
    clearLocalChat();
    window.location.href = '/chat';
  }

  const nav = [
    { href: '/chat',    label: UI_CONTENT.sidebar.chat,         icon: MessagesSquare, gated: false },
    { href: '/mentors', label: UI_CONTENT.sidebar.mentors,      icon: Users,          gated: true  },
    { href: '/account', label: UI_CONTENT.sidebar.account,      icon: UserCircle,     gated: true  },
    ...(role !== 'admin' ? [{ href: '/mentor', label: UI_CONTENT.sidebar.mentorPortal, icon: CalendarCheck, gated: false }] : []),
    ...(role === 'admin' ? [{ href: '/admin', label: UI_CONTENT.sidebar.admin, icon: ShieldCheck, gated: false }] : []),
  ];

  return (
    <>
      {/* ── Fixed top header: logo left · auth right ───────────── */}
      <div className="md:hidden sticky top-0 z-40 h-14 bg-[linear-gradient(180deg,#0F2C6B_0%,#0B1E4D_100%)] text-white shadow-lg flex items-center justify-between px-4">
        <Link href="/chat" aria-label="Immigroov home">
          <Image
            src="/Immigroov_Transparent_Logo.png"
            alt="Immigroov"
            width={280}
            height={60}
            priority
            className="object-contain brightness-0 invert"
            style={{ height: '22px', width: 'auto' }}
          />
        </Link>

        <div className="flex items-center gap-2">
          {authed ? (
            <>
              <div className="h-8 w-8 rounded-full bg-gradient-to-br from-brand-600 to-accent-500 flex items-center justify-center text-white text-xs font-bold select-none">
                {(email?.[0] ?? 'U').toUpperCase()}
              </div>
              <button
                onClick={handleSignOut}
                disabled={signingOut}
                aria-label="Sign out"
                className="h-8 w-8 rounded-full bg-white/15 flex items-center justify-center disabled:opacity-50 active:scale-95 transition-transform"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <Link
              href={`${pathname}?auth=open&mode=login`}
              className="h-9 px-3 rounded-full bg-white/15 text-xs font-semibold flex items-center gap-1.5 hover:bg-white/25 active:scale-95 transition-transform"
            >
              <LogIn className="h-3.5 w-3.5" />
              Login
            </Link>
          )}
        </div>
      </div>

      {/* ── Overlay (behind nav panel, above content) ──────────── */}
      <div
        className={cn(
          'md:hidden fixed inset-0 z-40 bg-brand-950/60 backdrop-blur-sm transition-opacity duration-300',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        )}
        onClick={() => setOpen(false)}
      />

      {/* ── Nav panel: slides down from below the header ────────── */}
      <div
        className={cn(
          'md:hidden fixed top-14 left-0 right-0 z-50',
          'bg-[linear-gradient(180deg,#0F2C6B_0%,#0B1E4D_100%)]',
          'overflow-hidden transition-[max-height] duration-300 ease-in-out',
          open ? 'max-h-80' : 'max-h-0',
        )}
      >
        <div className="flex flex-col py-2 px-3 gap-1">
          {nav.map(({ href, label, icon: Icon, gated }, i) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={hrefFor(href, gated)}
                onClick={() => setOpen(false)}
                className={cn(
                  'flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium text-white',
                  'transition-all',
                  active
                    ? 'bg-white/[0.18] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12)]'
                    : 'bg-white/[0.06] hover:bg-white/[0.13]',
                  open ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2',
                )}
                style={{ transitionDelay: open ? `${i * 50}ms` : '0ms' }}
              >
                <Icon className={cn('h-4 w-4 shrink-0', active ? 'text-accent-300' : 'text-white/60')} />
                {label}
              </Link>
            );
          })}
        </div>
      </div>

      {/* ── Floating hamburger FAB (bottom) ─────────────────────── */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? 'Close menu' : 'Open menu'}
        className={cn(
          'md:hidden fixed bottom-6 right-5 z-50',
          'h-14 w-14 rounded-full shadow-[0_4px_24px_rgba(0,0,0,0.35)]',
          'flex items-center justify-center',
          'transition-all duration-200 active:scale-95',
          open
            ? 'bg-white text-brand-900 rotate-90'
            : 'bg-[#0F2C6B] text-white',
        )}
      >
        {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>
    </>
  );
}
