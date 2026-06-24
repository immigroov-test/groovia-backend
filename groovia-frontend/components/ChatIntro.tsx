'use client';
import { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { ChevronDown, Compass, Users, Zap } from 'lucide-react';
import { UI_CONTENT } from '../lib/content';
import { cn } from '../lib/utils';

const FEATURE_ICONS = [Compass, Users, Zap];

// Headline types itself out; caret hides when done.
function TypeText({ text, speed = 45, className }: { text: string; speed?: number; className?: string }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const id = setInterval(() => {
      setShown((n) => {
        if (n >= text.length) {
          clearInterval(id);
          return n;
        }
        return n + 1;
      });
    }, speed);
    return () => clearInterval(id);
  }, [text, speed]);

  const done = shown >= text.length;
  return (
    <span className={className}>
      {text.slice(0, shown)}
      {!done && <span className="text-accent-500 animate-pulse">▍</span>}
    </span>
  );
}

const fadeUp = (delay: number) => ({
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.35, delay, ease: [0.4, 0, 0.2, 1] as const },
});

export function ChatIntro({ onStart }: { onStart?: () => void }) {
  const hero = UI_CONTENT.hero;
  const sectionRef = useRef<HTMLElement>(null);
  const [atTop, setAtTop] = useState(true);

  useEffect(() => {
    // Walk up to find the nearest scrollable ancestor
    let el: Element | null = sectionRef.current?.parentElement ?? null;
    while (el) {
      const { overflowY } = window.getComputedStyle(el);
      if (overflowY === 'auto' || overflowY === 'scroll') break;
      el = el.parentElement;
    }
    if (!el) return;
    const scrollEl = el;
    const onScroll = () => setAtTop(scrollEl.scrollTop < 10);
    scrollEl.addEventListener('scroll', onScroll, { passive: true });
    return () => scrollEl.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <section ref={sectionRef} className="relative min-h-full overflow-hidden flex flex-col">
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center text-center px-5 sm:px-8 py-12">
        <h1 className="text-4xl sm:text-6xl font-bold tracking-tight min-h-[1.2em] bg-gradient-to-r from-brand-900 via-brand-700 to-accent-600 bg-clip-text text-transparent">
          <TypeText text={hero.title} />
        </h1>

        <motion.p
          {...fadeUp(0.75)}
          className="mt-3 text-lg sm:text-xl font-semibold text-accent-600"
        >
          {hero.tagline}
        </motion.p>

        <motion.p
          {...fadeUp(1.1)}
          className="mt-7 max-w-2xl text-sm sm:text-base font-semibold text-brand-800"
        >
          {hero.movement}
        </motion.p>

        <div className="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4 max-w-3xl w-full">
          {hero.features.map((text, i) => {
            const Icon = FEATURE_ICONS[i % FEATURE_ICONS.length];
            return (
              <motion.div key={text} {...fadeUp(1.25 + i * 0.12)}>
                {/* Gradient-border glass card */}
                <div className="group h-full rounded-2xl p-[1px] bg-gradient-to-br from-brand-200/80 via-transparent to-accent-200/80 hover:from-brand-300 hover:to-accent-300 transition-colors">
                  <div className="h-full rounded-2xl bg-card/80 backdrop-blur-md px-4 py-4 flex items-start gap-3 text-left transition-transform duration-200 group-hover:-translate-y-0.5">
                    <span className="h-8 w-8 shrink-0 rounded-lg bg-gradient-to-br from-brand-700 to-accent-500 text-white flex items-center justify-center shadow-[0_4px_14px_-4px_rgba(245,158,11,0.5)]">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="text-sm leading-relaxed text-foreground/75">{text}</span>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>

      <motion.button
        {...fadeUp(1.75)}
        onClick={onStart}
        aria-hidden={!atTop}
        className={cn(
          "relative z-10 mx-auto mb-7 flex flex-col items-center gap-2 text-sm font-medium text-brand-700 hover:text-brand-900",
          "transition-opacity duration-300",
          atTop ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none",
        )}
      >
        {hero.scrollCta}
        <span className="flex flex-col items-center -space-y-2.5">
          {[0, 1, 2].map((i) => (
            <ChevronDown
              key={i}
              className="h-5 w-5 animate-arrow-cascade"
              style={{ animationDelay: `${i * 0.25}s` }}
            />
          ))}
        </span>
      </motion.button>
    </section>
  );
}
