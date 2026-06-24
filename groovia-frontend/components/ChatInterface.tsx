'use client';

import React, { useState, useEffect, useRef } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { v4 as uuidv4 } from 'uuid';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Paperclip, Send, Sparkles, Lock } from 'lucide-react';
import { UI_CONTENT, INTENT_OPTIONS } from '../lib/content';
import { createClient } from '../lib/supabase/client';
import { FEATURES } from '../lib/features';
import { LS_KEYS, clearLocalChat } from '../lib/chatStorage';
import { cn } from '../lib/utils';
import { ChatIntro } from './ChatIntro';

interface Props {
  authed: boolean;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

const MD_COMPONENTS: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  a: (props) => (
    <a
      {...props}
      target="_blank"
      rel="noopener noreferrer"
      className="!text-brand-700 !underline !underline-offset-4 hover:!text-brand-900 font-medium"
    />
  ),
};

async function authHeaders(): Promise<Record<string, string>> {
  const supabase = createClient();
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};
}

// Cap cached messages well under the localStorage quota; backend keeps full history.
const MAX_MESSAGES_PERSISTED = 50;

function loadFromStorage<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  const raw = window.localStorage.getItem(key);
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function safeSetMessages(messages: ChatMessage[]): void {
  if (typeof window === 'undefined') return;
  // Keep the last N. The visible UI still shows the full array — only the cache is trimmed.
  const trimmed = messages.length > MAX_MESSAGES_PERSISTED
    ? messages.slice(-MAX_MESSAGES_PERSISTED)
    : messages;
  try {
    window.localStorage.setItem(LS_KEYS.messages, JSON.stringify(trimmed));
  } catch {
    // Quota exceeded — drop and continue.
  }
}

// ↓ Adjust these two values to tune the visual balance
const LANDMARKS_OPACITY = 0.12;       // 0.0 = invisible  · 1.0 = fully visible
const CHAT_INPUT_OPACITY = 0.92;      // 0.7 = see-through · 1.0 = fully white

export default function ChatInterface({ authed }: Props) {
  const router = useRouter();
  const pathname = usePathname();

  // Guests become "gated" after resume upload — input disables, AuthGateRenderer shows the modal.
  const gated = !authed;

  function openGate() {
    router.push(`${pathname}?auth=open`);
  }

  // SSR-safe defaults; hydrated from localStorage in a single effect after mount.
  const [threadId, setThreadId] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'assistant', content: UI_CONTENT.welcomeMessage },
  ]);
  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [resumeUploaded, setResumeUploaded] = useState(false);
  const [intentSelected, setIntentSelected] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // One-time client hydration from localStorage. A fresh tab (no sessionStorage flag)
  // with a stale "resume uploaded" guest state is treated as a new visit and reset.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const isFreshSession = !window.sessionStorage.getItem('groovia.sessionStarted');
    window.sessionStorage.setItem('groovia.sessionStarted', '1');

    const storedResumeUploaded = loadFromStorage<boolean>(LS_KEYS.resumeUploaded, false);

    if (isFreshSession && !authed && storedResumeUploaded) {
      // Guest reopened the browser — clear stale gated state and start fresh.
      clearLocalChat();
      const fresh = uuidv4();
      window.localStorage.setItem(LS_KEYS.threadId, JSON.stringify(fresh));
      setThreadId(fresh);
      setHydrated(true);
      return;
    }

    const storedThread = loadFromStorage<string | null>(LS_KEYS.threadId, null);
    if (storedThread) {
      setThreadId(storedThread);
    } else {
      const fresh = uuidv4();
      window.localStorage.setItem(LS_KEYS.threadId, JSON.stringify(fresh));
      setThreadId(fresh);
    }
    const storedMessages = loadFromStorage<ChatMessage[] | null>(LS_KEYS.messages, null);
    if (storedMessages) setMessages(storedMessages);
    setResumeUploaded(storedResumeUploaded);
    setIntentSelected(loadFromStorage<boolean>(LS_KEYS.intentSelected, false));
    setHydrated(true);
  }, [authed]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!hydrated) return;
    safeSetMessages(messages);
  }, [messages, hydrated]);
  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(LS_KEYS.resumeUploaded, JSON.stringify(resumeUploaded));
  }, [resumeUploaded, hydrated]);
  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(LS_KEYS.intentSelected, JSON.stringify(intentSelected));
  }, [intentSelected, hydrated]);

  // When a guest signs in, link the guest thread to their account so it appears in history.
  useEffect(() => {
    if (!hydrated || !authed || !threadId) return;
    (async () => {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) return;
      const res = await fetch(`/api/chat/threads/${threadId}/claim`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      }).catch(() => null);
      if (res?.ok) {
        window.dispatchEvent(new CustomEvent('groovia:history-refresh'));
      }
    })();
  }, [hydrated, authed, threadId]);

  // Auto-resume the user's most recent thread on sign-in, only if the local chat is empty.
  useEffect(() => {
    if (!hydrated || !authed || !FEATURES.chatPersist) return;
    if (resumeUploaded || messages.length > 1) return;
    (async () => {
      const headers = await authHeaders();
      if (!Object.keys(headers).length) return;
      const tRes = await fetch('/api/chat/threads?limit=1', { headers, cache: 'no-store' }).catch(() => null);
      if (!tRes?.ok) return;
      const tData = await tRes.json();
      const last = tData.threads?.[0];
      if (!last?.id || last.id === threadId) return;

      const mRes = await fetch(`/api/chat/threads/${last.id}/messages`, { headers, cache: 'no-store' }).catch(() => null);
      if (!mRes?.ok) return;
      const mData = await mRes.json();
      const restored: ChatMessage[] = (mData.messages || []).filter(
        (m: { role: string; content: string }) => m.role === 'user' || m.role === 'assistant',
      );
      if (!restored.length) return;

      setThreadId(last.id);
      setMessages(restored);
      setResumeUploaded(true);
      setIntentSelected(true);
      window.localStorage.setItem(LS_KEYS.threadId, JSON.stringify(last.id));
    })();
  }, [hydrated, authed, resumeUploaded, messages.length, threadId]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const chatStartRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Stay at the top while the entry hero is showing.
    if (!resumeUploaded && messages.length <= 1) return;
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, resumeUploaded]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, [input]);

  async function postChat(formData: FormData) {
    const headers = await authHeaders();
    const res = await fetch('/api/chat', { method: 'POST', headers, body: formData });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    if (resumeUploaded) return;
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';

    setMessages((prev) => [...prev, { role: 'user', content: UI_CONTENT.uploadIndicator }]);
    setLoading(true);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('message', '[SYSTEM_RESUME_UPLOADED]');
    formData.append('thread_id', threadId);

    try {
      const data = await postChat(formData);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: data.response || UI_CONTENT.errors.noResponse },
      ]);
      setResumeUploaded(true);

      // Guests: open the auth gate. Modal won't close until they sign up / sign in.
      if (!authed) openGate();
    } catch {
      setMessages((prev) => [...prev, { role: 'assistant', content: UI_CONTENT.errors.backendUnreachable }]);
    } finally {
      setLoading(false);
    }
  }

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    // Guests must sign in before sending any message.
    if (gated) {
      openGate();
      return;
    }

    setMessages((prev) => [...prev, { role: 'user', content: trimmed }]);
    setInput('');
    setIntentSelected(true);
    setLoading(true);

    const formData = new FormData();
    formData.append('message', trimmed);
    formData.append('thread_id', threadId);

    try {
      const data = await postChat(formData);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: data.response || UI_CONTENT.errors.noResponse },
      ]);
      // First real turn just created/updated the thread row — refresh history so
      // the new title or thread shows up in the sidebar.
      if (authed) window.dispatchEvent(new CustomEvent('groovia:history-refresh'));
    } catch {
      setMessages((prev) => [...prev, { role: 'assistant', content: UI_CONTENT.errors.backendUnreachable }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Landmarks — fixed to viewport bottom, always visible regardless of chat state.
          z-index 0 puts it above the body background but below the z-1/z-10 content layers. */}
      <div
        className="fixed bottom-0 left-0 md:left-64 right-0 pointer-events-none select-none"
        style={{ zIndex: 0 }}
        aria-hidden
      >
        <img src="/landmarks.png" alt="" className="w-full block" style={{ opacity: LANDMARKS_OPACITY }} />
      </div>

      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileUpload}
        accept=".pdf,.docx"
        className="hidden"
        disabled={resumeUploaded}
      />

      {/* z-index: 1 creates a stacking context above the fixed landmarks (z-0),
          so message bubbles and the ChatIntro render on top of the image. */}
      <div className="flex-1 overflow-y-auto relative" style={{ zIndex: 1 }}>
        {/* Hero shown only before the conversation starts. */}
        {!resumeUploaded && messages.length <= 1 && (
          <ChatIntro onStart={() => chatStartRef.current?.scrollIntoView({ behavior: 'smooth' })} />
        )}

        <div ref={chatStartRef} className="mx-auto max-w-3xl px-4 pt-8 pb-44 space-y-6">
          {messages.map((m, i) => (
            <div
              key={i}
              className={cn('flex gap-3 animate-fade-up', m.role === 'user' ? 'justify-end' : 'justify-start')}
            >
              {m.role === 'assistant' && (
                <div className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-brand-700 to-accent-500 flex items-center justify-center text-white text-xs font-semibold">
                  <Sparkles className="h-3.5 w-3.5" />
                </div>
              )}
              <div
                className={cn(
                  'max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed',
                  m.role === 'user'
                    ? 'bg-brand-900 text-white rounded-br-sm'
                    : 'bg-brand-50/60 text-foreground rounded-bl-sm prose-chat',
                )}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                  {m.content}
                </ReactMarkdown>
              </div>
            </div>
          ))}

          {loading && (
            // Centered thinking indicator; mix-blend-multiply drops the GIF's white background.
            <div className="fixed inset-0 z-40 flex items-center justify-center pointer-events-none bg-background/10 backdrop-blur-sm">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/groovia-loop.gif"
                alt="Groovia is thinking…"
                className="h-24 w-auto mix-blend-multiply select-none"
              />
            </div>
          )}

          {resumeUploaded && !intentSelected && !loading && (
            <div className="pt-2 animate-fade-up">
              <p className="text-sm font-medium text-foreground mb-3">{UI_CONTENT.intentPrompt}</p>
              <div className="flex flex-wrap gap-2">
                {INTENT_OPTIONS.map(({ label, message }) => (
                  <button
                    key={label}
                    onClick={() => sendMessage(message)}
                    disabled={loading}
                    className="px-3.5 py-2 text-sm font-medium rounded-full bg-brand-50/70 text-brand-900 hover:bg-brand-100 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>
      </div>

      {/* z-index: 10 keeps the input bar above both the scroll area (z-1) and landmarks (z-0). */}
      <div className="bg-transparent relative" style={{ zIndex: 10 }}>
        <div className="mx-auto max-w-3xl px-4 py-4">
          {gated && resumeUploaded && (
            <button
              onClick={openGate}
              className="w-full flex items-center justify-center gap-2 mb-2 px-4 py-2.5 rounded-xl bg-accent-50 text-accent-700 hover:bg-accent-100 text-sm font-medium"
            >
              <Lock className="h-4 w-4" />
              {UI_CONTENT.signInToContinue}
            </button>
          )}

          <div
            className={cn(
              "flex items-end gap-2 rounded-2xl px-2 py-1.5",
              gated && resumeUploaded && "opacity-60",
            )}
            style={{ backgroundColor: `rgba(255,255,255,${CHAT_INPUT_OPACITY})` }}
          >
            {FEATURES.resumeUpload && (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={loading || resumeUploaded}
                title={resumeUploaded ? UI_CONTENT.tooltips.resumeAlreadyUploaded : UI_CONTENT.tooltips.attachResume}
                className="h-9 w-9 flex items-center justify-center rounded-lg text-muted hover:text-foreground hover:bg-brand-50/40 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <Paperclip className="h-4 w-4" />
              </button>
            )}

            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage(input);
                }
              }}
              placeholder={gated && resumeUploaded ? UI_CONTENT.inputPlaceholderLocked : UI_CONTENT.inputPlaceholder}
              disabled={gated && resumeUploaded}
              className="flex-1 bg-transparent border-none outline-none text-sm leading-relaxed resize-none py-2 max-h-40 disabled:cursor-not-allowed"
            />

            <button
              type="button"
              onClick={() => sendMessage(input)}
              disabled={loading || !input.trim() || (gated && resumeUploaded)}
              className="h-9 w-9 flex items-center justify-center rounded-lg bg-brand-900 text-white hover:bg-brand-800 disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>

          <p className="text-center text-xs text-muted mt-3 px-4">{UI_CONTENT.disclaimer}</p>
        </div>
      </div>
    </div>
  );
}
