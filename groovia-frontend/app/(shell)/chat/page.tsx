import ChatInterface from '../../../components/ChatInterface';
import { createClient } from '../../../lib/supabase/server';

export const metadata = { title: 'Chat — Immigroov' };

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ t?: string }>;
}) {
  const { t } = await searchParams;

  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  return <ChatInterface key={t ?? 'main'} authed={!!user} />;
}
