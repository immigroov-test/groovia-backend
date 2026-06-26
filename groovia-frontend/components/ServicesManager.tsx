'use client';
import { useEffect, useState } from 'react';
import { Loader2, Plus, Trash2, ToggleLeft, ToggleRight, ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from './ui/Button';
import { Input } from './ui/Input';
import { Card, CardBody } from './ui/Card';
import { cn } from '../lib/utils';

interface Service {
  id: string;
  title: string;
  description: string | null;
  type: string;
  duration: number;
  category: string | null;
  set_price: number;
  set_currency: string;
  platform_fee: number;
  is_active: boolean;
  is_ppp: boolean;
}

interface Question {
  id: string;
  question_text: string;
  is_required: boolean;
  question_type: string;
}

async function apiFetch(path: string, method = 'GET', body?: object) {
  const supabase = (await import('../lib/supabase/client')).createClient();
  const { data: { session } } = await supabase.auth.getSession();
  const res = await fetch(path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export function ServicesManager() {
  const [services, setServices]     = useState<Service[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [creating, setCreating]     = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [questions, setQuestions]   = useState<Record<string, Question[]>>({});

  const [form, setForm] = useState({
    title: '', description: '', type: 'video', duration: 30,
    category: '', set_price: '', is_ppp: false,
  });
  const [formError, setFormError]     = useState<string | null>(null);
  const [submitting, setSubmitting]   = useState(false);
  const [newQuestion, setNewQuestion] = useState<Record<string, { text: string; required: boolean }>>({});

  async function load() {
    setLoading(true); setError(null);
    try { setServices(await apiFetch('/api/mentor/services')); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function loadQuestions(serviceId: string) {
    try {
      const data = await apiFetch(`/api/mentor/services/${serviceId}/questions`);
      setQuestions(q => ({ ...q, [serviceId]: data }));
    } catch { /* ignore */ }
  }

  function toggleExpand(id: string) {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id);
    if (!questions[id]) loadQuestions(id);
  }

  async function createService() {
    if (!form.title.trim()) { setFormError('Title is required.'); return; }
    const duration = parseInt(String(form.duration));
    if (!duration || duration < 5 || duration > 480) { setFormError('Duration must be 5–480 minutes.'); return; }
    const price = parseFloat(form.set_price) || 0;
    setFormError(null); setSubmitting(true);
    try {
      await apiFetch('/api/mentor/services', 'POST', {
        title:       form.title.trim(),
        description: form.description.trim() || null,
        type:        form.type,
        duration,
        category:    form.category.trim() || null,
        set_price:   price,
        is_active:   true,
        is_ppp:      form.is_ppp,
      });
      setCreating(false);
      setForm({ title: '', description: '', type: 'video', duration: 30, category: '', set_price: '', is_ppp: false });
      await load();
    } catch (e: any) { setFormError(e.message); }
    finally { setSubmitting(false); }
  }

  async function toggleActive(id: string, current: boolean) {
    try {
      await apiFetch(`/api/mentor/services/${id}/active`, 'POST', { is_active: !current });
      setServices(s => s.map(svc => svc.id === id ? { ...svc, is_active: !current } : svc));
    } catch (e: any) { setError(e.message); }
  }

  async function deleteService(id: string) {
    if (!confirm('Delete this service? This cannot be undone.')) return;
    try {
      await apiFetch(`/api/mentor/services/${id}/delete`, 'POST');
      setServices(s => s.filter(svc => svc.id !== id));
    } catch (e: any) { setError(e.message); }
  }

  async function addQuestion(serviceId: string) {
    const q = newQuestion[serviceId];
    if (!q?.text?.trim()) return;
    try {
      await apiFetch(`/api/mentor/services/${serviceId}/questions`, 'POST', {
        service_id:    serviceId,
        question_text: q.text.trim(),
        is_required:   q.required ?? false,
        question_type: 'text',
      });
      setNewQuestion(nq => ({ ...nq, [serviceId]: { text: '', required: false } }));
      await loadQuestions(serviceId);
    } catch (e: any) { setError(e.message); }
  }

  async function deleteQuestion(serviceId: string, questionId: string) {
    try {
      await apiFetch(`/api/mentor/services/questions/${questionId}/delete`, 'POST');
      setQuestions(qs => ({
        ...qs,
        [serviceId]: qs[serviceId].filter(q => q.id !== questionId),
      }));
    } catch (e: any) { setError(e.message); }
  }

  if (loading) return (
    <div className="flex items-center gap-2 text-sm text-muted py-6">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading services…
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      {error && <p className="text-sm text-red-600">{error}</p>}

      {services.length === 0 && !creating && (
        <p className="text-sm text-muted">
          You haven&apos;t set up any session types yet. Create one below.
        </p>
      )}

      {services.map(svc => (
        <div key={svc.id} className="rounded-xl border border-[--color-border] overflow-hidden">
          <div className="flex items-start justify-between gap-3 p-4">
            <div className="flex flex-col gap-0.5 flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-semibold text-foreground">{svc.title}</span>
                <span className={cn(
                  'text-xs px-1.5 py-0.5 rounded-full font-medium',
                  svc.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-neutral-100 text-muted',
                )}>
                  {svc.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
              <p className="text-xs text-muted">
                {svc.duration}m · {svc.type === 'video' ? 'Video' : 'DM'} · {svc.set_price === 0 ? 'Free' : `${svc.set_currency} ${svc.set_price}`}
              </p>
              {svc.description && (
                <p className="text-xs text-muted mt-1 line-clamp-2">{svc.description}</p>
              )}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button onClick={() => toggleActive(svc.id, svc.is_active)} title={svc.is_active ? 'Deactivate' : 'Activate'}
                className="p-1.5 rounded-lg text-muted hover:text-foreground hover:bg-neutral-100 transition-colors">
                {svc.is_active
                  ? <ToggleRight className="h-4 w-4 text-emerald-600" />
                  : <ToggleLeft className="h-4 w-4" />
                }
              </button>
              <button onClick={() => deleteService(svc.id)} title="Delete"
                className="p-1.5 rounded-lg text-muted hover:text-red-600 hover:bg-red-50 transition-colors">
                <Trash2 className="h-4 w-4" />
              </button>
              <button onClick={() => toggleExpand(svc.id)} title="Questions"
                className="p-1.5 rounded-lg text-muted hover:text-foreground hover:bg-neutral-100 transition-colors">
                {expandedId === svc.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {expandedId === svc.id && (
            <div className="border-t border-[--color-border] bg-neutral-50/60 p-4 flex flex-col gap-3">
              <p className="text-xs font-medium text-muted uppercase tracking-wide">Intake questions</p>
              {(questions[svc.id] ?? []).map(q => (
                <div key={q.id} className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-foreground">{q.question_text}</p>
                    <p className="text-xs text-muted">{q.is_required ? 'Required' : 'Optional'}</p>
                  </div>
                  <button onClick={() => deleteQuestion(svc.id, q.id)}
                    className="p-1 text-muted hover:text-red-600 transition-colors shrink-0">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
              <div className="flex gap-2 mt-1">
                <input
                  type="text"
                  placeholder="Add a question…"
                  value={newQuestion[svc.id]?.text ?? ''}
                  onChange={e => setNewQuestion(nq => ({ ...nq, [svc.id]: { ...nq[svc.id], text: e.target.value, required: nq[svc.id]?.required ?? false } }))}
                  className="flex-1 h-8 px-3 text-xs rounded-lg border border-[--color-border] focus:outline-none focus:ring-2 focus:ring-brand-300 bg-white"
                />
                <label className="flex items-center gap-1 text-xs text-muted cursor-pointer whitespace-nowrap">
                  <input type="checkbox"
                    checked={newQuestion[svc.id]?.required ?? false}
                    onChange={e => setNewQuestion(nq => ({ ...nq, [svc.id]: { ...nq[svc.id], required: e.target.checked, text: nq[svc.id]?.text ?? '' } }))}
                  />
                  Required
                </label>
                <Button variant="outline" onClick={() => addQuestion(svc.id)}
                  className="h-8 px-3 text-xs">
                  Add
                </Button>
              </div>
            </div>
          )}
        </div>
      ))}

      {creating ? (
        <Card>
          <CardBody className="pt-5 flex flex-col gap-4">
            <h3 className="text-sm font-semibold text-foreground">New session type</h3>
            <Input label="Title *" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              placeholder="e.g. 30-min Career Q&A" />
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium text-foreground">Description</label>
              <textarea rows={2} value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="What will you cover?"
                className="px-3 py-2 rounded-lg bg-white text-sm text-foreground resize-none placeholder:text-muted shadow-[0_0_0_1px_rgba(15,23,42,0.06)] focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-foreground">Type</label>
                <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
                  className="h-10 px-3 rounded-lg bg-white text-sm border border-[--color-border] focus:outline-none focus:ring-2 focus:ring-brand-300">
                  <option value="video">Video call</option>
                  <option value="dm">DM / Text</option>
                </select>
              </div>
              <Input label="Duration (min) *" type="number" min={5} max={480}
                value={String(form.duration)}
                onChange={e => setForm(f => ({ ...f, duration: parseInt(e.target.value) || 30 }))} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Input label="Category" value={form.category}
                onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                placeholder="e.g. CV Review" />
              <Input label="Price (USD)" type="number" min={0} step={0.01}
                value={form.set_price}
                onChange={e => setForm(f => ({ ...f, set_price: e.target.value }))}
                placeholder="0 = free" />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={form.is_ppp}
                onChange={e => setForm(f => ({ ...f, is_ppp: e.target.checked }))} />
              Enable purchasing-power parity pricing
            </label>
            {formError && <p className="text-sm text-red-600">{formError}</p>}
            <div className="flex gap-2">
              <Button variant="accent" onClick={createService} loading={submitting}>Save</Button>
              <Button variant="outline" onClick={() => setCreating(false)}>Cancel</Button>
            </div>
          </CardBody>
        </Card>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 text-sm font-medium text-brand-700 hover:text-brand-900 transition-colors"
        >
          <Plus className="h-4 w-4" /> Add session type
        </button>
      )}
    </div>
  );
}
