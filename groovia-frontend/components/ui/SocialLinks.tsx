'use client';
import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { cn } from '../../lib/utils';

export interface SocialLink {
  type: string;
  url: string;
}

const PLATFORM_OPTIONS = [
  { value: 'linkedin',  label: 'LinkedIn',         icon: '🔗', domain: 'linkedin.com' },
  { value: 'github',    label: 'GitHub',            icon: '🐙', domain: 'github.com' },
  { value: 'twitter',   label: 'Twitter / X',       icon: '🐦', domain: null },
  { value: 'youtube',   label: 'YouTube',           icon: '▶️', domain: 'youtube.com' },
  { value: 'instagram', label: 'Instagram',         icon: '📸', domain: 'instagram.com' },
  { value: 'tiktok',    label: 'TikTok',            icon: '🎵', domain: 'tiktok.com' },
  { value: 'website',   label: 'Personal Website',  icon: '🌐', domain: null },
];

const PLATFORM_MAP = Object.fromEntries(PLATFORM_OPTIONS.map((p) => [p.value, p]));

function platformPlaceholder(type: string) {
  switch (type) {
    case 'linkedin':  return 'https://linkedin.com/in/yourname';
    case 'github':    return 'https://github.com/yourname';
    case 'twitter':   return 'https://x.com/yourhandle';
    case 'youtube':   return 'https://youtube.com/@channel';
    case 'instagram': return 'https://instagram.com/yourhandle';
    case 'tiktok':    return 'https://tiktok.com/@yourhandle';
    case 'website':   return 'https://yourwebsite.com';
    default:          return 'https://';
  }
}

interface Props {
  value: SocialLink[];
  onChange: (next: SocialLink[]) => void;
  hint?: string;
}

export function SocialLinks({ value, onChange, hint }: Props) {
  const [addType, setAddType] = useState('linkedin');
  const [addUrl, setAddUrl] = useState('');
  const [urlError, setUrlError] = useState('');

  function validateUrl(url: string, type: string): string {
    if (!url) return 'URL is required';
    if (!url.startsWith('https://')) return 'URL must start with https://';
    const platform = PLATFORM_MAP[type];
    if (platform?.domain) {
      try {
        const hostname = new URL(url).hostname.replace(/^www\./, '');
        if (!hostname.endsWith(platform.domain)) return `URL must be a ${platform.label} link`;
      } catch { return 'Invalid URL'; }
    }
    return '';
  }

  function addLink() {
    const err = validateUrl(addUrl.trim(), addType);
    if (err) { setUrlError(err); return; }
    if (value.some((l) => l.type === addType)) {
      setUrlError(`You already have a ${PLATFORM_MAP[addType]?.label} link`);
      return;
    }
    onChange([...value, { type: addType, url: addUrl.trim() }]);
    setAddUrl('');
    setUrlError('');
  }

  function remove(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Existing links */}
      {value.map((link, i) => {
        const platform = PLATFORM_MAP[link.type];
        return (
          <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand-50 border border-brand-100">
            <span className="text-base">{platform?.icon ?? '🔗'}</span>
            <span className="text-xs font-medium text-brand-700 shrink-0 w-20">{platform?.label ?? link.type}</span>
            <span className="text-xs text-muted flex-1 truncate">{link.url}</span>
            <button
              type="button"
              onClick={() => remove(i)}
              className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-red-600 shrink-0"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}

      {/* Add row */}
      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <select
            value={addType}
            onChange={(e) => { setAddType(e.target.value); setAddUrl(''); setUrlError(''); }}
            className={cn(
              'h-10 px-2 rounded-lg text-sm bg-white text-foreground shrink-0',
              'shadow-[0_0_0_1px_rgba(15,23,42,0.06)] focus:outline-none focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]',
            )}
          >
            {PLATFORM_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>{p.icon} {p.label}</option>
            ))}
          </select>
          <input
            type="url"
            value={addUrl}
            onChange={(e) => { setAddUrl(e.target.value); setUrlError(''); }}
            placeholder={platformPlaceholder(addType)}
            className={cn(
              'flex-1 h-10 px-3 rounded-lg text-sm bg-white text-foreground',
              'shadow-[0_0_0_1px_rgba(15,23,42,0.06)] focus:outline-none',
              urlError
                ? 'shadow-[0_0_0_1px_rgba(220,38,38,0.5)]'
                : 'focus:shadow-[0_0_0_2px_rgba(29,78,216,0.25)]',
              'placeholder:text-muted',
            )}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addLink(); } }}
          />
          <button
            type="button"
            onClick={addLink}
            className="h-10 w-10 flex items-center justify-center rounded-lg bg-brand-600 text-white hover:bg-brand-700 shrink-0"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
        {urlError && <p className="text-xs text-red-600">{urlError}</p>}
      </div>

      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
