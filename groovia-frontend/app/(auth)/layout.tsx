import Link from 'next/link';
import { X } from 'lucide-react';
import { Logo } from '../../components/ui/Logo';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col hero-gradient">
      <main className="flex-1 flex items-center justify-center px-4 py-8">
        <div className="relative w-full max-w-md animate-fade-up">
          <Link
            href="/chat"
            aria-label="Close"
            className="absolute -top-2 -right-2 p-1.5 rounded-full text-muted hover:text-foreground hover:bg-black/5 transition-colors"
          >
            <X className="h-4 w-4" />
          </Link>
          <div className="flex justify-center mb-6">
            <Logo />
          </div>
          {children}
        </div>
      </main>
      <footer className="px-6 py-4 text-xs text-muted flex items-center justify-between">
        <span>© {new Date().getFullYear()} Immigroov</span>
        <div className="flex gap-4">
          <Link href="/privacy" className="hover:text-foreground">Privacy</Link>
          <Link href="/terms" className="hover:text-foreground">Terms</Link>
        </div>
      </footer>
    </div>
  );
}
