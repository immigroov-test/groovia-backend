'use client';
import { useCallback, useRef, useState } from 'react';
import { Camera, Upload, X } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';
import { cn } from '../../lib/utils';

interface Props {
  value: string | null;
  onChange: (url: string | null) => void;
  userId: string;
  required?: boolean;
}

export function PhotoUpload({ value, onChange, userId, required }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const upload = useCallback(async (file: File) => {
    setError('');
    if (!file.type.startsWith('image/')) { setError('Please select an image file (PNG or JPG).'); return; }
    if (file.size > 5 * 1024 * 1024) { setError('Image must be under 5 MB.'); return; }

    setUploading(true);
    try {
      const supabase = createClient();
      const ext = file.name.split('.').pop() ?? 'jpg';
      const path = `${userId}/avatar.${ext}`;
      const { error: uploadErr } = await supabase.storage
        .from('mentor-photos')
        .upload(path, file, { upsert: true });

      if (uploadErr) throw uploadErr;

      const { data } = supabase.storage.from('mentor-photos').getPublicUrl(path);
      onChange(data.publicUrl + `?t=${Date.now()}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  }, [userId, onChange]);

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) upload(file);
    e.target.value = '';
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) upload(file);
  }

  return (
    <div className="flex flex-col gap-2">
      <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={onFileChange} />

      {value ? (
        <div className="relative w-24 h-24">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={value} alt="Profile photo" className="w-24 h-24 rounded-full object-cover border-2 border-brand-100 shadow-sm" />
          <button
            type="button"
            onClick={() => onChange(null)}
            className="absolute -top-1 -right-1 h-6 w-6 rounded-full bg-red-600 text-white flex items-center justify-center shadow hover:bg-red-700"
          >
            <X className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="absolute -bottom-1 -right-1 h-7 w-7 rounded-full bg-brand-600 text-white flex items-center justify-center shadow hover:bg-brand-700"
          >
            <Camera className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={cn(
            'w-full border-2 border-dashed rounded-xl px-6 py-8 flex flex-col items-center gap-2 cursor-pointer transition-colors',
            dragging
              ? 'border-brand-500 bg-brand-50'
              : 'border-[--color-border] hover:border-brand-300 hover:bg-brand-50/40',
            uploading && 'pointer-events-none opacity-60',
          )}
        >
          <div className="h-10 w-10 rounded-full bg-brand-100 flex items-center justify-center">
            {uploading
              ? <div className="h-4 w-4 rounded-full border-2 border-brand-600 border-t-transparent animate-spin" />
              : <Upload className="h-5 w-5 text-brand-600" />}
          </div>
          <div className="text-center">
            <p className="text-sm font-medium text-brand-700">
              {uploading ? 'Uploading…' : 'Drag and drop your photo here, or click to browse'}
            </p>
            <p className="text-xs text-muted mt-0.5">PNG, JPG or WebP · up to 5 MB</p>
          </div>
        </div>
      )}

      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
