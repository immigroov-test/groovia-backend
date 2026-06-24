'use client';
// Top-level error boundary. Renders whenever a Server Component or Client Component
// throws above the (shell) layout. Keeps a single broken page from crashing the app.
import { useEffect } from 'react';
import { Button } from '../components/ui/Button';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console for now. Wire to Sentry when added.
    console.error('Page crashed:', error);
  }, [error]);

  return (
    <html lang="en">
      <body className="min-h-screen flex items-center justify-center px-4 bg-background">
        <div className="max-w-md text-center">
          <h1 className="text-2xl font-semibold text-brand-900">Something went wrong.</h1>
          <p className="mt-3 text-sm text-foreground/70">
            Our team has been notified. You can try again, or head back to chat.
          </p>
          <div className="mt-6 flex items-center justify-center gap-2">
            <Button onClick={reset}>Try again</Button>
            <a href="/chat">
              <Button variant="outline">Back to chat</Button>
            </a>
          </div>
          {error.digest && (
            <p className="mt-4 text-[11px] text-muted font-mono">ref: {error.digest}</p>
          )}
        </div>
      </body>
    </html>
  );
}
