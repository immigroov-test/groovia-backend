'use client';
import { usePathname, useRouter } from 'next/navigation';
import { Card, CardBody } from './ui/Card';
import { Button } from './ui/Button';

export function MentorAuthTrigger() {
  const router = useRouter();
  const pathname = usePathname();

  function openSignup() {
    router.push(`${pathname}?auth=open&role=mentor`);
  }

  function openLogin() {
    router.push(`${pathname}?auth=open&role=mentor&mode=login`);
  }

  return (
    <Card>
      <CardBody className="pt-6">
        <h2 className="text-base font-semibold text-foreground">Ready to become a mentor?</h2>
        <p className="text-sm text-muted mt-1">
          Share your experience with people making international career moves. Create a free mentor
          account to get started.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="accent" onClick={openSignup}>Create mentor account</Button>
          <Button variant="outline" onClick={openLogin}>Sign in</Button>
        </div>
      </CardBody>
    </Card>
  );
}
