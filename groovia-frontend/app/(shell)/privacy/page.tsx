export const metadata = { title: 'Privacy Policy — Immigroov' };

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12 prose prose-slate">
      <h1 className="text-3xl font-semibold tracking-tight text-brand-900">Privacy Policy</h1>
      <p className="text-sm text-muted mt-2">Last updated: pending legal review</p>
      <p className="mt-6 text-foreground/80">
        This page is a placeholder. The Immigroov privacy policy will be finalized after Netherlands-qualified
        GDPR review. It will cover:
      </p>
      <ul className="mt-4 list-disc pl-5 text-foreground/80 space-y-1.5 text-sm">
        <li>Personal data we collect (account, chat, uploaded documents)</li>
        <li>How we use and retain it</li>
        <li>Third-party processors (Supabase, Groq, Stripe, Resend)</li>
        <li>Your rights (access, export, deletion) under GDPR</li>
        <li>Cookie usage and consent</li>
        <li>Contact for data requests</li>
      </ul>
    </div>
  );
}
