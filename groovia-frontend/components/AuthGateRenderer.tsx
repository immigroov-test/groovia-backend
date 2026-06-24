'use client';

interface Props {
  authed: boolean;
}

// Gated nav links now redirect to /signup?next=<path> directly — no modal needed.
export function AuthGateRenderer(_props: Props) {
  return null;
}
