// Right-aligned auth chip: shows email + sign-out for authed users, "Sign in" for guests.
import { createClient } from '../lib/supabase/server';
import { TopBarClient } from './TopBarClient';

export async function TopBar() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  return <TopBarClient email={user?.email ?? null} />;
}
