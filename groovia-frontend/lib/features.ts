// Mirror of the backend's config.FEATURE_* values; reads NEXT_PUBLIC_FEATURE_<NAME> at build time.

function flag(name: string, defaultOn = true): boolean {
  const raw = process.env[`NEXT_PUBLIC_FEATURE_${name}`];
  if (raw === undefined) return defaultOn;
  const v = raw.trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

export const FEATURES = {
  chatHistory:           flag('CHAT_HISTORY',   false),  // recent-chats sidebar; off by default
  chatPersist:           flag('CHAT_PERSIST'),            // auto-resume last thread on sign-in; on by default
  guestMode:             flag('GUEST_MODE'),
  mentorsPublic:         flag('MENTORS_PUBLIC'),
  resumeUpload:          flag('RESUME_UPLOAD'),
  googleOAuth:           flag('GOOGLE_OAUTH'),
  // UI-only flags (no backend equivalent needed)
  cookieConsent:         flag('COOKIE_CONSENT', false),  // not implemented yet — off
  analytics:             flag('ANALYTICS',      false),  // not implemented yet — off
  idleLogout:            flag('IDLE_LOGOUT',    false),  // auto sign-out after inactivity; off by default
} as const;

// Minutes of inactivity before idleLogout signs the user out.
export const IDLE_TIMEOUT_MINUTES = Number(process.env.NEXT_PUBLIC_IDLE_TIMEOUT_MINUTES) || 30;
