'use client';
import { useEffect } from 'react';
import { createClient } from '../lib/supabase/client';
import { FEATURES, IDLE_TIMEOUT_MINUTES } from '../lib/features';
import { clearLocalChat } from '../lib/chatStorage';

const LAST_ACTIVITY_KEY = 'groovia.lastActivity';
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];
const CHECK_INTERVAL_MS = 30_000;

function markActive() {
  window.localStorage.setItem(LAST_ACTIVITY_KEY, String(Date.now()));
}

export function IdleLogout({ authed }: { authed: boolean }) {
  useEffect(() => {
    if (!FEATURES.idleLogout || !authed) return;

    const timeoutMs = IDLE_TIMEOUT_MINUTES * 60_000;
    markActive();
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, markActive, { passive: true });
    document.addEventListener('visibilitychange', markActive);

    const interval = setInterval(async () => {
      const last = Number(window.localStorage.getItem(LAST_ACTIVITY_KEY) || Date.now());
      if (Date.now() - last < timeoutMs) return;

      const supabase = createClient();
      await supabase.auth.signOut();
      clearLocalChat();
      window.location.href = '/chat?auth=open&mode=login';
    }, CHECK_INTERVAL_MS);

    return () => {
      for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, markActive);
      document.removeEventListener('visibilitychange', markActive);
      clearInterval(interval);
    };
  }, [authed]);

  return null;
}
