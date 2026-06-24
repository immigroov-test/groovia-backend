import Link from 'next/link';
import { Button } from '../../components/ui/Button';
import { Card, CardBody } from '../../components/ui/Card';
import { Badge } from '../../components/ui/Badge';

export default function LandingPage() {
  return (
    <>
      {/* Hero */}
      <section className="hero-gradient">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 sm:py-28">
          <div className="flex flex-col items-center text-center animate-fade-up">
            <Badge tone="accent" className="mb-5">
              <span className="mr-1">✦</span> Now in early access
            </Badge>
            <h1 className="text-4xl sm:text-5xl md:text-6xl font-semibold tracking-tight text-brand-900 max-w-3xl">
              Your immigration journey
              <span className="block text-accent-600">doesn&apos;t have to be lonely.</span>
            </h1>
            <p className="mt-5 text-lg text-muted max-w-2xl">
              AI-powered guidance and mentors who have actually made the move.
              Discover countries that fit your story, prep your CV, and book real conversations.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link href="/?auth=open">
                <Button size="lg">Start with Groovia</Button>
              </Link>
              <Link href="/mentors">
                <Button size="lg" variant="outline">Browse mentors</Button>
              </Link>
            </div>
            <p className="mt-4 text-xs text-muted">
              Free to start. No card required.
            </p>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="py-20 sm:py-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="text-center mb-12">
            <p className="text-sm font-medium text-accent-600 mb-2">How it works</p>
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-brand-900">
              Three steps to a clearer path
            </h2>
          </div>
          <div className="grid gap-6 md:grid-cols-3">
            {[
              {
                step: '01',
                title: 'Chat with Groovia',
                desc: 'Tell our AI about your background and goals. Upload your CV — it extracts the rest.',
              },
              {
                step: '02',
                title: 'Get matched',
                desc: '3–5 countries that fit your skills, budget, and timeline. Each with a visa pathway and a real mentor.',
              },
              {
                step: '03',
                title: 'Book a mentor',
                desc: 'Talk to someone who already lives there. Honest answers, no fluff, real timelines.',
              },
            ].map((s) => (
              <Card key={s.step} className="animate-fade-up">
                <CardBody className="pt-6">
                  <div className="text-accent-500 font-mono text-sm font-medium">{s.step}</div>
                  <h3 className="mt-2 text-lg font-semibold text-brand-900">{s.title}</h3>
                  <p className="mt-2 text-sm text-muted leading-relaxed">{s.desc}</p>
                </CardBody>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* CTA strip */}
      <section className="py-20 bg-brand-900 text-white">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 text-center">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">
            Ready when you are.
          </h2>
          <p className="mt-3 text-brand-100/90 max-w-xl mx-auto">
            Start the conversation. Decide later whether you want to talk to a human.
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
            <Link href="/signup">
              <Button size="lg" variant="accent">Try Groovia for free</Button>
            </Link>
            <Link href="/mentors">
              <Button size="lg" variant="outline" className="border-white/20 bg-white/5 text-white hover:bg-white/10">
                Meet the mentors
              </Button>
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
