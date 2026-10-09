import { useEffect, useState } from "react";
import {
  detectExtension,
  extensionIdFromStoreUrl,
  runInvestigation,
  type InvestigationResult,
  type ProgressEvent,
} from "./extensionBridge";
import { VerdictResult } from "./components/VerdictResult";
import {
  EXTENSION,
  GEN_Z_STATS,
  HELPLINE,
  SAFETY_RULES,
  SCAM_TYPES,
  SOURCES,
  STATS,
  STEPS,
  VERDICT_POINTS,
} from "./content";

/* ------------------------------------------------------------------ utils */

function cx(...parts: Array<string | false | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function Container({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cx("mx-auto w-full max-w-6xl px-6 sm:px-8", className)}>{children}</div>;
}

function Eyebrow({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "paper" }) {
  return (
    <p className={cx("eyebrow", tone === "paper" ? "text-paper/60" : "text-muted")}>{children}</p>
  );
}

/** The animated-free brand mark: a lens with the finding struck through it. */
function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className} fill="none">
      <circle cx="11" cy="11" r="7.25" stroke="currentColor" strokeWidth="1.75" />
      <path d="M14.6 14.6 20 20" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
      <path d="M7.6 11h6.8" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

/* ----------------------------------------------------------------- header */

const NAV = [
  { href: "#problem", label: "The problem" },
  { href: "#how", label: "How it works" },
  { href: "#result", label: "What you get" },
  { href: "#safety", label: "Safety rules" },
];

function Header({ onGetExtension }: { onGetExtension: () => void }) {
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-paper/85 backdrop-blur-md">
      <Container className="flex h-16 items-center justify-between gap-6">
        <a href="#top" className="flex items-center gap-2.5 text-ink">
          <Mark className="h-6 w-6 text-signal" />
          <span className="font-display text-[17px] font-semibold tracking-tight">
            Fakee
          </span>
        </a>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Sections">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="text-sm text-muted transition-colors hover:text-ink"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <button
          type="button"
          onClick={onGetExtension}
          className="shrink-0 rounded-full bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink-soft"
        >
          Get the extension
        </button>
      </Container>
    </header>
  );
}

/* ------------------------------------------------------------------- hero */

function VerdictPreview() {
  return (
    <div className="relative">
      <div className="absolute -inset-3 -z-10 rounded-[26px] bg-paper-deep/70" aria-hidden="true" />
      <article className="grain relative overflow-hidden rounded-2xl border border-line bg-paper p-6 shadow-[0_18px_50px_-30px_rgba(20,17,12,0.45)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Eyebrow>Investigation · example</Eyebrow>
            <h3 className="mt-2 font-display text-lg font-semibold">QuickHyre AI Solutions</h3>
            <p className="text-sm text-muted">AI Data Collection Associate · Bangalore</p>
          </div>
          <div className="text-right">
            <div className="font-display text-4xl font-semibold leading-none text-crimson">92</div>
            <div className="eyebrow mt-1">/ 100</div>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-crimson/10 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-crimson uppercase">
            Critical risk
          </span>
          <span className="rounded-full border border-line px-2.5 py-1 text-[11px] font-medium text-muted">
            Fraudulent (verified)
          </span>
        </div>

        <p className="mt-4 text-sm leading-relaxed text-ink-soft">
          A fee demanded alongside selection without an interview is a textbook advance-fee scam.
        </p>

        <ul className="mt-5 space-y-3 border-t border-line pt-4">
          {[
            ["Upfront payment requested", "+68", "₹1,500 registration fee"],
            ["Selected without an interview", "+42", "no assessment of any kind"],
            ["Recruitment via WhatsApp", "+9", "no formal application channel"],
          ].map(([label, points, note]) => (
            <li key={label} className="flex items-start gap-3">
              <span className="mt-0.5 font-mono text-xs font-semibold text-crimson">{points}</span>
              <span className="min-w-0">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-xs text-muted">{note}</span>
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-4 text-xs text-muted">
          <span>12 pages read</span>
          <span aria-hidden="true">·</span>
          <span>3 independent sources</span>
          <span aria-hidden="true">·</span>
          <span>domain registered 20 days ago</span>
        </div>
      </article>
    </div>
  );
}

function Hero({ onGetExtension }: { onGetExtension: () => void }) {
  return (
    <section id="top" className="relative overflow-hidden border-b border-line">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-gradient-to-b from-paper-deep/60 to-transparent" />
      <Container className="relative grid items-center gap-14 py-20 sm:py-28 lg:grid-cols-[1.05fr_0.95fr] lg:gap-20">
        <div>
          <Eyebrow>Recruitment fraud in India</Eyebrow>
          <h1 className="mt-5 font-display text-[2.6rem] font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Know who you're
            <br />
            applying to.
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-soft">
            Fake jobs and internships are not a fringe risk — they are an organised industry. This
            tool investigates a posting the way a careful person would: it searches, reads, checks the
            domain, and shows you the evidence behind the verdict.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={onGetExtension}
              className="rounded-full bg-signal px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-signal/90"
            >
              Get the browser extension
            </button>
            <a
              href="#how"
              className="rounded-full border border-line-strong px-6 py-3 text-sm font-medium text-ink transition-colors hover:bg-paper-deep"
            >
              See how it works
            </a>
          </div>

          <dl className="mt-12 grid max-w-lg grid-cols-3 gap-6 border-t border-line pt-7">
            <div>
              <dt className="eyebrow">Searches</dt>
              <dd className="mt-1 font-display text-2xl font-semibold">In your browser</dd>
            </div>
            <div>
              <dt className="eyebrow">Verdict</dt>
              <dd className="mt-1 font-display text-2xl font-semibold">Explainable</dd>
            </div>
            <div>
              <dt className="eyebrow">Cost</dt>
              <dd className="mt-1 font-display text-2xl font-semibold">Free</dd>
            </div>
          </dl>
        </div>

        <VerdictPreview />
      </Container>
    </section>
  );
}

/* ---------------------------------------------------------------- problem */

function Problem() {
  return (
    <section id="problem" className="bg-ink text-paper">
      <Container className="py-20 sm:py-28">
        <div className="max-w-3xl">
          <Eyebrow tone="paper">The scale of it</Eyebrow>
          <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-5xl">
            The last thing a scam wants is for you to check.
          </h2>
          <p className="mt-6 text-paper/70 leading-relaxed">
            Recruitment fraud works because applying is an act of trust. You are told you were
            selected, that the seat is limited, that the letter is attached. Checking feels
            ungrateful. That asymmetry is the entire business model.
          </p>
        </div>

        <div className="mt-14 grid gap-px overflow-hidden rounded-xl border border-paper/15 bg-paper/15 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((stat) => (
            <article key={stat.label} className="bg-ink p-6">
              <div className="font-display text-4xl font-semibold text-paper">{stat.figure}</div>
              <p className="mt-3 text-sm font-medium text-paper">{stat.label}</p>
              <p className="mt-2 text-xs leading-relaxed text-paper/50">{stat.detail}</p>
              <p className="mt-4 eyebrow text-paper/40">{stat.source}</p>
            </article>
          ))}
        </div>

        <div className="mt-12 grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
          <div>
            <Eyebrow tone="paper">Who pays the price</Eyebrow>
            <h3 className="mt-4 font-display text-2xl font-semibold">
              Early-career applicants and students
            </h3>
            <p className="mt-4 text-sm leading-relaxed text-paper/60">
              Those with the least experience of how hiring really works, and the most to lose from
              missing a genuine opening.
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-7">
            {GEN_Z_STATS.map((item) => (
              <div key={item.label} className="border-t border-paper/15 pt-4">
                <dt className="font-display text-3xl font-semibold">{item.figure}</dt>
                <dd className="mt-2 text-xs leading-relaxed text-paper/60">{item.label}</dd>
              </div>
            ))}
          </dl>
        </div>
      </Container>
    </section>
  );
}

/* ------------------------------------------------------------- scam types */

function ScamTypes() {
  return (
    <section className="border-b border-line">
      <Container className="py-20 sm:py-28">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-2xl">
            <Eyebrow>How they operate</Eyebrow>
            <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
              Six shapes the same fraud takes
            </h2>
          </div>
          <p className="max-w-sm text-sm leading-relaxed text-muted">
            The details change; the mechanics rarely do. Learn the mechanics and you stop needing to
            memorise the details.
          </p>
        </div>

        <div className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {SCAM_TYPES.map((scam) => (
            <article
              key={scam.title}
              className="flex flex-col rounded-xl border border-line bg-paper p-6 transition-colors hover:border-line-strong"
            >
              <span className="eyebrow">{scam.tag}</span>
              <h3 className="mt-3 font-display text-xl font-semibold">{scam.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted">{scam.body}</p>
            </article>
          ))}
        </div>
      </Container>
    </section>
  );
}

/* ----------------------------------------------------------- how it works */

function HowItWorks() {
  return (
    <section id="how" className="border-b border-line bg-paper-deep/50">
      <Container className="py-20 sm:py-28">
        <div className="max-w-2xl">
          <Eyebrow>What it does, and how</Eyebrow>
          <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
            An investigation, not a coin flip
          </h2>
          <p className="mt-6 leading-relaxed text-ink-soft">
            Most “scam checkers” ask a model whether a posting looks fake. This one gathers evidence
            first, then reasons over it — and keeps the reasoning in the open.
          </p>
        </div>

        <ol className="mt-16 grid gap-x-12 gap-y-12 md:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((step) => (
            <li key={step.n} className="border-t border-line-strong pt-6">
              <div className="flex items-baseline gap-4">
                <span className="font-mono text-sm text-signal">{step.n}</span>
                <h3 className="font-display text-xl font-semibold">{step.title}</h3>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-muted">{step.body}</p>
              {step.note && (
                <p className="mt-4 font-mono text-[11px] tracking-wide text-faint uppercase">
                  {step.note}
                </p>
              )}
            </li>
          ))}
        </ol>

        <div className="mt-16 rounded-xl border border-line bg-paper p-8">
          <div className="grid gap-8 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <h3 className="font-display text-xl font-semibold">
                Your browsing is the search engine
              </h3>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted">
                The extension performs the searches and reads the pages locally, in your own browser
                session. That keeps the tool free to run and keeps it honest: the pages you are shown
                as evidence are pages the tool actually read.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 lg:justify-end">
              {["DuckDuckGo", "Optional Brave", "No API keys needed"].map((chip) => (
                <span
                  key={chip}
                  className="rounded-full border border-line-strong px-3 py-1.5 text-xs font-medium text-ink"
                >
                  {chip}
                </span>
              ))}
            </div>
          </div>
        </div>
      </Container>
    </section>
  );
}

/* ---------------------------------------------------------------- verdict */

function Verdict() {
  return (
    <section id="result" className="border-b border-line">
      <Container className="py-20 sm:py-28">
        <div className="grid gap-14 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20">
          <div>
            <Eyebrow>What you get</Eyebrow>
            <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
              A result you could defend out loud
            </h2>
            <p className="mt-6 leading-relaxed text-ink-soft">
              The point is not to be told “safe” or “scam”. It is to be handed the evidence and the
              reasoning, so you can decide — and so you can see exactly where the tool is unsure.
            </p>
            <div className="mt-10 space-y-7">
              {VERDICT_POINTS.map((point) => (
                <div key={point.title} className="border-t border-line pt-5">
                  <h3 className="font-display text-lg font-semibold">{point.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted">{point.body}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="lg:pt-16">
            <div className="rounded-2xl border border-line bg-paper p-7">
              <Eyebrow>Anatomy of a result</Eyebrow>
              <ul className="mt-6 space-y-5">
                {[
                  ["Risk score & level", "A 0–100 number and a band, from rules — reproducible."],
                  ["Detected signals", "Each with severity, weight and the text that triggered it."],
                  ["Verified / unverified", "What the investigation could confirm, and what it could not."],
                  ["Evidence coverage", "Pages captured, pages analysed, signal sentences found."],
                  ["Sources", "The exact URLs the verdict rests on, so you can read them yourself."],
                ].map(([title, body]) => (
                  <li key={title} className="flex gap-4">
                    <span
                      className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-signal"
                      aria-hidden="true"
                    />
                    <span>
                      <span className="block text-sm font-semibold">{title}</span>
                      <span className="block text-sm leading-relaxed text-muted">{body}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="mt-4 rounded-2xl border border-crimson/25 bg-crimson/[0.04] p-7">
              <Eyebrow>It also says this</Eyebrow>
              <p className="mt-4 font-display text-xl font-semibold text-ink">
                “Not enough evidence to judge either way.”
              </p>
              <p className="mt-3 text-sm leading-relaxed text-muted">
                When a search returns nothing about the company, the tool refuses to call it safe.
                An absence of evidence is reported as an absence of evidence.
              </p>
            </div>
          </div>
        </div>
      </Container>
    </section>
  );
}

/* -------------------------------------------------------------- check gate */

function CheckPanel({ onGetExtension }: { onGetExtension: () => void }) {
  // The extension id is read from the store listing URL. Until the listing
  // exists it is empty, and the panel falls back to the install gate.
  const extensionId = extensionIdFromStoreUrl(EXTENSION.storeUrl);

  const [text, setText] = useState("");
  const [extensionReady, setExtensionReady] = useState(false);
  const [checked, setChecked] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showGate, setShowGate] = useState(false);
  const ready = text.trim().length >= 12;

  // Detect the extension once on load so the button can do the real thing.
  useEffect(() => {
    if (!extensionId) {
      setChecked(true);
      return;
    }
    let alive = true;
    detectExtension(extensionId).then((found) => {
      if (!alive) return;
      setExtensionReady(found);
      setChecked(true);
    });
    return () => {
      alive = false;
    };
  }, [extensionId]);

  async function analyse() {
    setError(null);
    setResult(null);
    setShowGate(false);

    if (!extensionReady) {
      setShowGate(true);
      return;
    }

    setRunning(true);
    setProgress({ phase: "Starting…" });
    try {
      setResult(await runInvestigation({ extensionId, text: text.trim(), onProgress: setProgress }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      // If the extension vanished mid-run, offer the install route instead.
      if (/extension|not available|respond/i.test(message)) setShowGate(true);
    } finally {
      setRunning(false);
      setProgress(null);
    }
  }

  const progressLabel = progress
    ? `${progress.phase}${progress.total ? ` · ${progress.done ?? 0}/${progress.total}` : ""}`
    : "";

  return (
    <section id="check" className="border-b border-line bg-paper-deep/50">
      <Container className="py-20 sm:py-28">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.1fr] lg:items-start lg:gap-20">
          <div>
            <Eyebrow>Check a posting</Eyebrow>
            <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
              Paste a posting. Watch it get investigated.
            </h2>
            <p className="mt-6 leading-relaxed text-ink-soft">
              The searches and page reads happen inside the browser extension, on your own machine —
              which is what keeps the tool free and keeps your posting private. The verdict comes
              back here, with the evidence behind it.
            </p>
            <p className="mt-4 text-sm text-muted">
              Nothing you paste here is stored by this page.
            </p>
            {checked && extensionId && (
              <p className="mt-4 inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted">
                <span
                  className={cx(
                    "h-1.5 w-1.5 rounded-full",
                    extensionReady ? "bg-moss" : "bg-faint",
                  )}
                  aria-hidden="true"
                />
                {extensionReady ? "Extension detected" : "Extension not detected"}
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-line bg-paper p-7">
            <label htmlFor="posting" className="eyebrow">
              Job or internship details
            </label>
            <textarea
              id="posting"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setShowGate(false);
                setError(null);
              }}
              rows={6}
              spellCheck={false}
              disabled={running}
              placeholder={
                "Company: ABC Technologies\nInternship: Software Development Intern\nThey contacted me on WhatsApp. Selected without an interview.\nAsked for a ₹1,500 registration fee. Website: abc-careers.xyz"
              }
              className="mt-3 w-full resize-y rounded-xl border border-line bg-paper px-4 py-3 text-sm leading-relaxed text-ink placeholder:text-faint focus:border-signal focus:outline-none disabled:opacity-60"
            />

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={!ready || running}
                onClick={analyse}
                className={cx(
                  "rounded-full px-6 py-2.5 text-sm font-semibold transition-colors",
                  ready && !running
                    ? "bg-ink text-paper hover:bg-ink-soft"
                    : "cursor-not-allowed bg-line text-faint",
                )}
              >
                {running ? "Investigating…" : "Analyse this posting"}
              </button>
              <span className="text-xs text-muted">
                {running
                  ? "Reading pages in your browser…"
                  : "Runs in your browser, with the extension"}
              </span>
            </div>

            {running && (
              <div
                role="status"
                aria-live="polite"
                className="mt-5 rounded-xl border border-line bg-paper-deep/60 p-5"
              >
                <p className="text-sm font-medium">{progressLabel || "Starting…"}</p>
                {progress?.note && <p className="mt-1 text-xs text-muted">{progress.note}</p>}
                <div className="mt-3 h-1 overflow-hidden rounded-full bg-line">
                  <div
                    className={cx(
                      "h-full bg-signal transition-[width] duration-300",
                      progress?.total ? "" : "w-1/3 animate-pulse",
                    )}
                    style={
                      progress?.total
                        ? {
                            width: `${Math.min(100, Math.round(((progress.done ?? 0) / progress.total) * 100))}%`,
                          }
                        : undefined
                    }
                  />
                </div>
              </div>
            )}

            {error && (
              <p
                role="alert"
                className="mt-5 rounded-xl border border-crimson/30 bg-crimson/[0.05] p-4 text-sm text-crimson"
              >
                {error}
              </p>
            )}

            {showGate && !running && (
              <div
                role="status"
                className="mt-5 rounded-xl border border-signal/30 bg-signal-soft/60 p-5"
              >
                <p className="text-sm font-semibold text-signal">
                  The extension is required to analyse this.
                </p>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                  Searches and page reads happen in your browser, which is what keeps the tool free
                  and private. Install the extension and this button will run the investigation
                  right here.
                </p>
                <button
                  type="button"
                  onClick={onGetExtension}
                  className="mt-4 rounded-full bg-signal px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-signal/90"
                >
                  Get the extension
                </button>
              </div>
            )}

            {result && <VerdictResult result={result} />}
          </div>
        </div>
      </Container>
    </section>
  );
}

/* ------------------------------------------------------------------ safety */

function Safety() {
  return (
    <section id="safety" className="bg-ink text-paper">
      <Container className="py-20 sm:py-28">
        <div className="max-w-2xl">
          <Eyebrow tone="paper">If you remember nothing else</Eyebrow>
          <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
            Five rules that stop most of it
          </h2>
        </div>

        <ol className="mt-14 grid gap-px overflow-hidden rounded-xl border border-paper/15 bg-paper/15 md:grid-cols-2 lg:grid-cols-3">
          {SAFETY_RULES.map((item, index) => (
            <li key={item.rule} className="bg-ink p-7">
              <span className="font-mono text-xs text-signal">{String(index + 1).padStart(2, "0")}</span>
              <h3 className="mt-3 font-display text-xl font-semibold text-paper">{item.rule}</h3>
              <p className="mt-3 text-sm leading-relaxed text-paper/60">{item.body}</p>
            </li>
          ))}
          <li className="flex flex-col justify-between bg-signal p-7 text-white">
            <div>
              <h3 className="font-display text-xl font-semibold">Already a victim?</h3>
              <p className="mt-3 text-sm leading-relaxed text-white/80">
                Report it immediately. Fast reporting is what makes recovery possible.
              </p>
            </div>
            <div className="mt-6 space-y-1 text-sm font-medium">
              <p>
                Helpline{" "}
                <a
                  href={`tel:${HELPLINE.number}`}
                  className="underline underline-offset-4 hover:no-underline"
                >
                  {HELPLINE.number}
                </a>
              </p>
              <p>
                <a
                  href={HELPLINE.portalUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline underline-offset-4 hover:no-underline"
                >
                  {HELPLINE.portal}
                </a>
              </p>
            </div>
          </li>
        </ol>
      </Container>
    </section>
  );
}

/* ----------------------------------------------------------------- install */

function Install() {
  return (
    <section id="install" className="border-b border-line">
      <Container className="py-20 sm:py-28">
        <div className="grid gap-14 lg:grid-cols-[1fr_1fr] lg:gap-20">
          <div>
            <Eyebrow>Get the extension</Eyebrow>
            <h2 className="mt-5 font-display text-3xl font-semibold leading-tight sm:text-4xl">
              Install it once. Keep it open while you apply.
            </h2>
            <p className="mt-6 leading-relaxed text-ink-soft">
              The extension adds a side panel to Chrome. Paste a posting, watch the investigation
              progress, and read the verdict without leaving the page you were on.
            </p>

            {EXTENSION.storeUrl ? (
              <a
                href={EXTENSION.storeUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-8 inline-flex rounded-full bg-signal px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-signal/90"
              >
                Add to Chrome
              </a>
            ) : (
              <p className="mt-8 rounded-xl border border-line bg-paper-deep/60 px-5 py-4 text-sm text-muted">
                Not yet listed on the Chrome Web Store. Load it directly from the project folder using
                the steps on the right.
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-line bg-paper p-7">
            <Eyebrow>Manual install</Eyebrow>
            <ol className="mt-6 space-y-5">
              {EXTENSION.installSteps.map((step, index) => (
                <li key={step} className="flex gap-4">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line-strong font-mono text-xs">
                    {index + 1}
                  </span>
                  <span className="text-sm leading-relaxed text-ink-soft">{step}</span>
                </li>
              ))}
            </ol>
            <p className="mt-7 border-t border-line pt-5 font-mono text-[11px] tracking-wide text-faint uppercase">
              {EXTENSION.name} · v{EXTENSION.version} · Chrome {EXTENSION.chromeVersion}+
            </p>
          </div>
        </div>
      </Container>
    </section>
  );
}

/* ------------------------------------------------------------------ footer */

function Footer() {
  return (
    <footer className="bg-paper-deep/40">
      <Container className="py-16">
        <div className="grid gap-12 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <div className="flex items-center gap-2.5">
              <Mark className="h-5 w-5 text-signal" />
              <span className="font-display text-base font-semibold">Fakee</span>
            </div>
            <p className="mt-5 max-w-xl text-sm leading-relaxed text-muted">
              Signals are advisory. Always verify an employer through a channel you initiate
              yourself, and never pay for a job, internship or “training”. This tool reports what the
              evidence supports — including when it supports nothing.
            </p>
          </div>

          <div>
            <Eyebrow>Figures reported by</Eyebrow>
            <ul className="mt-5 space-y-2">
              {SOURCES.map((source) => (
                <li key={source} className="text-sm text-muted">
                  {source}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="mt-14 flex flex-col gap-3 border-t border-line pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-faint">
            Statistics are quoted as reported by the bodies listed above and dated where stated.
          </p>
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
            <span>
              Report fraud: {HELPLINE.portal} · {HELPLINE.number}
            </span>
            <a href="/privacy" className="underline underline-offset-4 hover:no-underline">
              Privacy policy
            </a>
          </p>
        </div>
      </Container>
    </footer>
  );
}

/* --------------------------------------------------------------------- app */

export default function App() {
  const scrollToInstall = () => {
    document.getElementById("install")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="min-h-screen bg-paper">
      <Header onGetExtension={scrollToInstall} />
      <main>
        <Hero onGetExtension={scrollToInstall} />
        <Problem />
        <ScamTypes />
        <HowItWorks />
        <Verdict />
        <CheckPanel onGetExtension={scrollToInstall} />
        <Safety />
        <Install />
      </main>
      <Footer />
    </div>
  );
}
