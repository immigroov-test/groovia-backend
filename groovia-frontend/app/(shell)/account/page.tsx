import { createClient } from '../../../lib/supabase/server';
import { Card, CardBody } from '../../../components/ui/Card';
import { Badge } from '../../../components/ui/Badge';
import { ProfileEditor } from '../../../components/ProfileEditor';

export const metadata = { title: 'Account — Immigroov' };

export default async function AccountPage() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  const { data: profile } = await supabase
    .from('profiles')
    .select('full_name, email, phone, profile_summary, role')
    .eq('id', user!.id)
    .maybeSingle();

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-brand-900">Account</h1>
      <p className="text-sm text-muted mt-1">Your profile and contact details.</p>

      <div className="mt-8 grid gap-4">
        <Card>
          <CardBody className="pt-6">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3 min-w-0">
                <div className="h-11 w-11 shrink-0 rounded-full bg-gradient-to-br from-brand-700 to-accent-500 flex items-center justify-center text-white text-base font-semibold">
                  {(profile?.full_name?.[0] ?? profile?.email?.[0] ?? 'U').toUpperCase()}
                </div>
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-foreground truncate">
                    {profile?.full_name ?? '—'}
                  </h2>
                  <p className="text-sm text-muted truncate">{profile?.email}</p>
                </div>
              </div>
              <Badge tone="brand">{profile?.role}</Badge>
            </div>
          </CardBody>
        </Card>

        <ProfileEditor
          userId={user!.id}
          initialFullName={profile?.full_name ?? ''}
          initialPhone={profile?.phone ?? ''}
          initialSummary={profile?.profile_summary ?? ''}
        />
      </div>
    </div>
  );
}
