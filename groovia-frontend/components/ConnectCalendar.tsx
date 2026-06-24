'use client';
import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { CalendarCheck, CalendarPlus } from 'lucide-react';
import { Card, CardBody } from './ui/Card';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { createClient } from '../lib/supabase/client';

interface Props {
  mentorName: string;
  nylasEmail: string | null;
  connectedAt: string | null;
}

export function ConnectCalendar(props: Props) {
  // Wrap in Suspense because useSearchParams forces dynamic rendering.
  return (
    <Suspense fallback={null}>
      <ConnectCalendarInner {...props} />
    </Suspense>
  );
}

function ConnectCalendarInner({ mentorName, nylasEmail, connectedAt }: Props) {
  const searchParams = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connected = searchParams.get('connected') === '1';
  const errorParam = searchParams.get('error');

  async function connect() {
    setLoading(true);
    setError(null);
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        setError('You need to be signed in.');
        return;
      }
      const redirectUri = `${window.location.origin}/api/nylas/callback`;
      const res = await fetch(`/api/mentor/nylas/connect-url?redirect_uri=${encodeURIComponent(redirectUri)}`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const data = await res.json();
      if (!res.ok || !data.url) {
        setError('Could not start the connect flow. Please try again.');
        return;
      }
      window.location.href = data.url;
    } catch {
      setError('Could not start the connect flow. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardBody className="pt-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-foreground">{mentorName}</h2>
          {nylasEmail ? (
            <div className="mt-1 flex items-center gap-2 text-sm text-muted">
              <CalendarCheck className="h-4 w-4 text-emerald-600" />
              <span>Calendar connected — {nylasEmail}</span>
              <Badge tone="success">Connected</Badge>
            </div>
          ) : (
            <p className="text-sm text-muted mt-1">
              Connect your Google or Outlook calendar so candidates can book sessions with you.
            </p>
          )}
          {connected && <p className="text-sm text-emerald-600 mt-2">Calendar connected successfully.</p>}
          {errorParam && <p className="text-sm text-red-600 mt-2">Something went wrong connecting your calendar. Please try again.</p>}
          {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
        </div>
        <Button variant={nylasEmail ? 'outline' : 'accent'} onClick={connect} loading={loading}>
          <CalendarPlus className="h-4 w-4" />
          {nylasEmail ? 'Reconnect calendar' : 'Connect calendar'}
        </Button>
      </CardBody>
      {connectedAt && (
        <CardBody className="pt-0 -mt-2">
          <p className="text-xs text-muted">Connected on {new Date(connectedAt).toLocaleString()}</p>
        </CardBody>
      )}
    </Card>
  );
}
