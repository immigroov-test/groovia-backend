import { redirect } from 'next/navigation';
import { createClient } from '../../../lib/supabase/server';
import { backendBaseUrl } from '../../../lib/backend';
import { AdminMentorList } from '../../../components/AdminMentorList';
import { Card, CardBody } from '../../../components/ui/Card';
import { UI_CONTENT } from '../../../lib/content';

export const metadata = { title: 'Admin — Immigroov' };

export interface AdminMentor {
  id: string;
  slug: string;
  display_name: string;
  headline: string | null;
  status: string;
  created_at: string;
  email: string | null;
  full_name: string | null;
  submission_count: number;
}

interface AdminStats {
  pending_mentor_count: number;
  approved_mentor_count: number;
  total_bookings: number;
}

async function fetchMentors(url: string, token: string): Promise<AdminMentor[]> {
  try {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
    if (res.ok) return await res.json();
  } catch { /* render empty */ }
  return [];
}

export default async function AdminPage() {
  const supabase = await createClient();
  const { data: { session } } = await supabase.auth.getSession();

  if (!session) redirect('/login?next=/admin');

  const { data: profile } = await supabase
    .from('profiles')
    .select('role')
    .eq('id', session.user.id)
    .maybeSingle();

  if (profile?.role !== 'admin') redirect('/chat');

  const token = session.access_token;
  const base = backendBaseUrl();

  const [pending, approved, suspended, statsRes] = await Promise.all([
    fetchMentors(`${base}/admin/mentors/pending`, token),
    fetchMentors(`${base}/admin/mentors/approved`, token),
    fetchMentors(`${base}/admin/mentors/suspended`, token),
    fetch(`${base}/admin/stats`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
      .then((r) => r.ok ? r.json() as Promise<AdminStats> : null)
      .catch(() => null),
  ]);

  const stats: AdminStats = statsRes ?? {
    pending_mentor_count: pending.length,
    approved_mentor_count: approved.length,
    total_bookings: 0,
  };

  const t = UI_CONTENT.admin;

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-brand-900">{t.title}</h1>
      <p className="text-sm text-muted mt-1">{t.subtitle}</p>

      {/* Stats cards */}
      <div className="mt-6 grid grid-cols-3 gap-3">
        <Card>
          <CardBody className="pt-5 pb-5">
            <p className="text-2xl font-bold text-foreground">{stats.pending_mentor_count}</p>
            <p className="text-xs text-muted mt-0.5">Pending review</p>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="pt-5 pb-5">
            <p className="text-2xl font-bold text-foreground">{stats.approved_mentor_count}</p>
            <p className="text-xs text-muted mt-0.5">Active mentors</p>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="pt-5 pb-5">
            <p className="text-2xl font-bold text-foreground">{stats.total_bookings}</p>
            <p className="text-xs text-muted mt-0.5">Total bookings</p>
          </CardBody>
        </Card>
      </div>

      {/* Pending Applications */}
      <section className="mt-10">
        <h2 className="text-lg font-semibold text-foreground">{t.pendingTitle}</h2>
        <p className="text-sm text-muted mt-0.5 mb-4">{t.pendingSubtitle}</p>
        <AdminMentorList
          initialMentors={pending}
          actions={[
            { action: 'reject', label: t.reject, variant: 'outline', loadingKey: 'reject' },
            { action: 'approve', label: t.approve, variant: 'accent', loadingKey: 'approve' },
          ]}
        />
      </section>

      {/* Active Mentors */}
      <section className="mt-10">
        <h2 className="text-lg font-semibold text-foreground">{t.activeTitle}</h2>
        <p className="text-sm text-muted mt-0.5 mb-4">{t.activeSubtitle}</p>
        <AdminMentorList
          initialMentors={approved}
          actions={[
            { action: 'suspend', label: t.suspend, variant: 'outline', loadingKey: 'suspend' },
          ]}
        />
      </section>

      {/* Suspended Mentors */}
      {suspended.length > 0 && (
        <section className="mt-10">
          <h2 className="text-lg font-semibold text-foreground">{t.suspendedTitle}</h2>
          <p className="text-sm text-muted mt-0.5 mb-4">{t.suspendedSubtitle}</p>
          <AdminMentorList
            initialMentors={suspended}
            actions={[
              { action: 'reinstate', label: t.reinstate, variant: 'accent', loadingKey: 'reinstate' },
            ]}
          />
        </section>
      )}
    </div>
  );
}
