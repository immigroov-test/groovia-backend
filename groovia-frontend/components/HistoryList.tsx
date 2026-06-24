'use client';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Clock } from 'lucide-react';
import { createClient } from '../lib/supabase/client';
import { UI_CONTENT } from '../lib/content';
import { LS_KEYS } from '../lib/chatStorage';
import { cn } from '../lib/utils';

interface Thread {
  id: string;
  title: string | null;
  user_intent: string | null;
  last_message_at: string | null;
  message_count: number;
}

interface Props {
  open: boolean;
}

const REFRESH_EVENT = 'groovia:history-refresh';

function relativeTime(iso: string | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function labelFor(t: Thread): string {
  if (t.title) return t.title;
  if (t.user_intent === 'report') return 'Career report';
  if (t.user_intent === 'mentor') return 'Mentor search';
  if (t.user_intent === 'qna') return 'Q&A';
  return 'Conversation';
}

export function HistoryList({ open }: Props) {
  const router = useRouter();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchThreads = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) { setThreads([]); return; }
      const res = await fetch('/api/chat/threads', {
        headers: { Authorization: `Bearer ${session.access_token}` },
        cache: 'no-store',
      });
      const data = await res.json();
      setThreads(data.threads ?? []);
    } catch {
      setThreads([]);
    } finally {
      setLoading(false);
    }
  }, [open]);

  // Initial load + refresh on 'groovia:history-refresh' (fired after thread claim).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    fetchThreads();
    const handler = () => fetchThreads();
    window.addEventListener(REFRESH_EVENT, handler);
    return () => window.removeEventListener(REFRESH_EVENT, handler);
  }, [fetchThreads]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function loadThread(threadId: string) {
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) return;
      const res = await fetch(`/api/chat/threads/${threadId}/messages`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const data = await res.json();
      const messages = data.messages?.length
        ? data.messages
        : [{ role: 'assistant', content: UI_CONTENT.welcomeMessage }];

      window.localStorage.setItem(LS_KEYS.threadId, JSON.stringify(threadId));
      window.localStorage.setItem(LS_KEYS.messages, JSON.stringify(messages));
      window.localStorage.setItem(LS_KEYS.resumeUploaded, JSON.stringify(messages.length > 1));
      window.localStorage.setItem(LS_KEYS.intentSelected, JSON.stringify(messages.length > 2));

      router.push(`/chat?t=${threadId}`);
    } catch {
      // silent
    }
  }

  if (!open) return null;

  return (
    <div className="mt-6 px-3">
      <p className="px-2 mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
        {UI_CONTENT.sidebar.history}
      </p>
      {loading ? (
        <p className="px-2 text-xs text-muted">Loading…</p>
      ) : threads.length === 0 ? (
        <p className="px-2 text-xs text-muted">{UI_CONTENT.sidebar.historyEmpty}</p>
      ) : (
        <ul className="space-y-0.5">
          {threads.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => loadThread(t.id)}
                className={cn(
                  'w-full flex flex-col items-start gap-0.5 text-left px-3 py-2 rounded-md',
                  'text-sm text-foreground/80 hover:bg-brand-50/40 hover:text-foreground',
                )}
              >
                <span className="line-clamp-1 font-medium">{labelFor(t)}</span>
                <span className="inline-flex items-center gap-1 text-[11px] text-muted">
                  <Clock className="h-3 w-3" />
                  {relativeTime(t.last_message_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
