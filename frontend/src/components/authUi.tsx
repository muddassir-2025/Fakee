/** Small shared pieces used by the account and admin views. */
import type { ReactNode } from "react";

export function GoogleMark({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 18 18" className={className} aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.42 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}

/** The brand mark used in the header: a lens with the finding struck through. */
export function XIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="11" cy="11" r="7.25" stroke="currentColor" strokeWidth="1.75" />
      <path d="M14.6 14.6 20 20" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
      <path d="M7.6 11h6.8" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

export function Notice({
  tone,
  title,
  children,
}: {
  tone: "good" | "bad" | "warn";
  title: string;
  children: ReactNode;
}) {
  const styles = {
    good: "border-moss/30 bg-moss/[0.06]",
    bad: "border-crimson/30 bg-crimson/[0.05]",
    warn: "border-amber/30 bg-amber/[0.06]",
  }[tone];
  const heading = { good: "text-moss", bad: "text-crimson", warn: "text-amber" }[tone];
  return (
    <div role="status" className={`mt-6 rounded-2xl border p-4 ${styles}`}>
      <p className={`text-sm font-semibold ${heading}`}>{title}</p>
      <div className="mt-1 text-sm leading-relaxed text-ink-soft">{children}</div>
    </div>
  );
}
