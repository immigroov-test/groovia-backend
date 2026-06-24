'use client';
import { useState } from 'react';
import { createClient } from '../lib/supabase/client';
import { Card, CardBody } from './ui/Card';
import { Input } from './ui/Input';
import { Button } from './ui/Button';

interface Props {
  userId: string;
  initialFullName: string;
  initialPhone: string;
  initialSummary: string;
}

export function ProfileEditor({ userId, initialFullName, initialPhone, initialSummary }: Props) {
  const [fullName, setFullName] = useState(initialFullName);
  const [phone, setPhone] = useState(initialPhone);
  const [summary, setSummary] = useState(initialSummary);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const { error } = await supabase
      .from('profiles')
      .update({
        full_name: fullName.trim() || null,
        phone: phone.trim() || null,
        profile_summary: summary.trim() || null,
      })
      .eq('id', userId);
    setSaving(false);
    if (error) {
      setError(error.message);
      return;
    }
    setSavedAt(Date.now());
  }

  return (
    <Card>
      <CardBody className="pt-6">
        <h2 className="text-base font-semibold text-foreground mb-1">Profile</h2>
        <p className="text-xs text-muted mb-5">
          Your summary is filled in automatically when Groovia reads your resume — edit it anytime.
        </p>

        <div className="flex flex-col gap-4">
          <Input
            label="Full name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Your name"
            autoComplete="name"
          />
          <Input
            label="Phone (optional)"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+31 6 12345678"
            autoComplete="tel"
          />
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">Summary</span>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={5}
              maxLength={2000}
              placeholder="A short professional summary — your role, experience, skills and goals."
              className="w-full rounded-xl border border-[--color-border] bg-card px-3.5 py-2.5 text-sm leading-relaxed placeholder:text-muted/70 focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-400 resize-y"
            />
          </label>

          {error && <p className="text-xs text-red-600">{error}</p>}

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} loading={saving}>Save changes</Button>
            {savedAt && !saving && <span className="text-xs text-muted">Saved ✓</span>}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
