'use client';
// Animates children on route change — pathname is the AnimatePresence key.
import { motion, AnimatePresence } from 'motion/react';
import { usePathname } from 'next/navigation';
import { type ReactNode } from 'react';

export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
        className="contents"
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
