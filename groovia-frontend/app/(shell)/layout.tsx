import { createClient } from '../../lib/supabase/server';
import { Sidebar } from '../../components/Sidebar';
import { MobileNav } from '../../components/MobileNav';
import { TopBar } from '../../components/TopBar';
import { AuthModal } from '../../components/AuthModal';
import { PageTransition } from '../../components/PageTransition';
import { IdleLogout } from '../../components/IdleLogout';

export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  let role: string | null = null;
  if (user) {
    const { data: profile } = await supabase
      .from('profiles')
      .select('role')
      .eq('id', user.id)
      .maybeSingle();
    role = profile?.role ?? null;
  }

  return (
    <div className="flex flex-col md:flex-row h-screen overflow-hidden">
      <MobileNav authed={!!user} email={user?.email ?? null} role={role} />
      <Sidebar authed={!!user} role={role} />
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* TopBar handles auth on desktop only; MobileNav handles it on mobile */}
        <div className="hidden md:block">
          <TopBar />
        </div>
        <main className="flex-1 min-h-0 overflow-y-auto pb-24 md:pb-0">
          <PageTransition>{children}</PageTransition>
        </main>
      </div>
      <AuthModal />
      <IdleLogout authed={!!user} />
    </div>
  );
}
