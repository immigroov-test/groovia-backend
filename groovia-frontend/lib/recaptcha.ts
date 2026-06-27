// Loads Google reCAPTCHA v3 and executes it for a given action. No-op if no site key is configured.

declare global {
  interface Window {
    grecaptcha?: {
      ready: (cb: () => void) => void;
      execute: (siteKey: string, opts: { action: string }) => Promise<string>;
    };
  }
}

const SITE_KEY = process.env.NEXT_PUBLIC_RECAPTCHA_SITE_KEY;

export function recaptchaEnabled(): boolean {
  return !!SITE_KEY;
}

function loadScript(): Promise<void> {
  if (window.grecaptcha) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `https://www.google.com/recaptcha/api.js?render=${SITE_KEY}`;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load reCAPTCHA'));
    document.head.appendChild(script);
  });
}

export async function getRecaptchaToken(action: string): Promise<string | null> {
  if (!SITE_KEY) return null;
  // reCAPTCHA site keys are domain-locked; skip on localhost to avoid console errors.
  if (typeof window !== 'undefined' && window.location.hostname === 'localhost') return null;
  await loadScript();
  return new Promise((resolve) => {
    window.grecaptcha!.ready(async () => {
      try {
        resolve(await window.grecaptcha!.execute(SITE_KEY, { action }));
      } catch {
        resolve(null);
      }
    });
  });
}
