// Single source of truth for chat-related localStorage keys.
export const LS_KEYS = {
  threadId: 'groovia.threadId',
  messages: 'groovia.messages',
  resumeUploaded: 'groovia.resumeUploaded',
  intentSelected: 'groovia.intentSelected',
} as const;

export const LS_CHAT_KEYS = Object.values(LS_KEYS);

export function clearLocalChat(): void {
  if (typeof window === 'undefined') return;
  for (const k of LS_CHAT_KEYS) window.localStorage.removeItem(k);
}
