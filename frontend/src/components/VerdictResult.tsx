import type { ReactNode } from "react";
import type {
  AnalystReport,
  Evidence,
  EvidenceCoverage,
  InvestigationResult,
  RiskLevel,
  RiskSignal,
  TrustSignal,
} from "../extensionBridge";
import { STATUS_TEXT } from "../extensionBridge";

/**
 * Renders an investigation verdict inline on the website.
 *
 * This mirrors what the extension's side panel shows, in the site's own
 * editorial style: the score and status first, then the reasoning, then the
 * evidence a reader can check for themselves.
 */

const LEVEL_STYLE: Record<RiskLevel, { text: string; dot: string; label: string }> = {
  LOW: { text: "text-moss", dot: "bg-moss", label: "Low risk" },
  MODERATE: { text: "text-amber", dot: "bg-amber", label: "Moderate risk" },
  HIGH: { text: "text-signal", dot: "bg-signal", label: "High risk" },
  CRITICAL: { text: "text-crimson", dot: "bg-crimson", label: "Critical risk" },
};

const SEVERITY: Record<RiskSignal["severity"], { text: string; dot: string }> = {
  critical: { text: "text-crimson", dot: "bg-crimson" },
  high: { text: "text-signal", dot: "bg-signal" },
  medium: { text: "text-amber", dot: "bg-amber" },
  low: { text: "text-moss", dot: "bg-moss" },
  info: { text: "text-muted", dot: "bg-faint" },
};

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function VerdictResult({
  result,
  pagesCaptured,
}: {
  result: InvestigationResult;
  pagesCaptured?: number;
}) {
  const risk = result.risk;
  const level = LEVEL_STYLE[risk.level] ?? LEVEL_STYLE.MODERATE;
  const coverage = result.investigation?.coverage;
  const notable = result.investigation?.notable_findings ?? [];
  const evidence = result.investigation?.evidence?.length
    ? result.investigation.evidence
    : risk.evidence ?? [];

  return (
    <div className="mt-5 space-y-4" aria-live="polite">
      {/* ---------------------------------------------------------- verdict */}
      <section className="grain relative overflow-hidden rounded-2xl border border-line bg-paper p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow">Verdict</p>
            <div className="mt-3 flex items-baseline gap-3">
              <span className={cx("font-display text-5xl font-semibold leading-none", level.text)}>
                {risk.score}
              </span>
              <span className="text-sm text-faint">/ 100</span>
            </div>
          </div>
          <span
            className={cx(
              "rounded-full border border-line px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide",
              level.text,
            )}
          >
            {level.label}
          </span>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {risk.status && (
            <span className="rounded-full border border-line-strong px-2.5 py-1 text-[11px] font-medium text-muted">
              {STATUS_TEXT[risk.status] ?? risk.status}
            </span>
          )}
          {risk.deadline && (
            <span className="text-xs text-muted">
              Deadline: {risk.deadline}
              {risk.expired ? " (passed)" : ""}
            </span>
          )}
          {typeof risk.confidence === "number" && (
            <span className="text-xs text-faint">
              Confidence {Math.round(risk.confidence * 100)}%
            </span>
          )}
        </div>

        <h3 className="mt-5 font-display text-xl font-semibold">{risk.headline}</h3>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">{risk.summary}</p>

        {risk.recommendation && (
          <div className="mt-4 border-t border-line pt-4">
            <p className="eyebrow">What to do</p>
            <p className="mt-2 text-sm leading-relaxed text-ink-soft">{risk.recommendation}</p>
          </div>
        )}

        {result.cached && (
          <p className="mt-4 text-xs text-muted">
            Served from cache — a recent check of this company.
          </p>
        )}
      </section>

      {/* ----------------------------------------------------- what to verify */}
      {!!risk.checklist?.length && (
        <Panel title="What to verify before applying">
          <ul className="space-y-2 text-sm leading-relaxed text-ink-soft">
            {risk.checklist.map((item) => (
              <li key={item} className="flex gap-3">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-signal" aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* ------------------------------------------------------------- ai read */}
      {risk.analyst && <Analyst analyst={risk.analyst} />}

      {/* ------------------------------------------------------------ signals */}
      {!!risk.signals?.length && (
        <Panel title={`Detected signals (${risk.signals.length})`}>
          <ul className="space-y-3">
            {risk.signals.map((signal) => {
              const sev = SEVERITY[signal.severity] ?? SEVERITY.medium;
              return (
                <li key={signal.id} className="border-t border-line pt-3 first:border-t-0 first:pt-0">
                  <div className="flex items-start justify-between gap-3">
                    <span className="flex items-start gap-2.5">
                      <span
                        className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", sev.dot)}
                        aria-hidden="true"
                      />
                      <span className="text-sm font-medium">{signal.label}</span>
                    </span>
                    <span className="shrink-0 font-mono text-xs text-muted">+{signal.points}</span>
                  </div>
                  {signal.explanation && (
                    <p className="mt-1.5 pl-4.5 text-xs leading-relaxed text-muted">
                      {signal.explanation}
                    </p>
                  )}
                  {!!signal.evidence?.length && (
                    <p className="mt-1.5 pl-4.5 font-mono text-[11px] leading-relaxed text-faint">
                      “{signal.evidence[0]}”
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>
      )}

      {/* -------------------------------------------------------- positive side */}
      {!!risk.trust_signals?.length && (
        <Panel title={`What it gets right (trust ${risk.trust_score}/100)`}>
          <ul className="space-y-3">
            {risk.trust_signals.map((signal: TrustSignal) => (
              <li key={signal.id} className="border-t border-line pt-3 first:border-t-0 first:pt-0">
                <div className="flex items-start justify-between gap-3">
                  <span className="flex items-start gap-2.5">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-moss" aria-hidden="true" />
                    <span className="text-sm font-medium">{signal.label}</span>
                  </span>
                  <span className="shrink-0 font-mono text-xs text-moss">+{signal.points}</span>
                </div>
                {signal.explanation && (
                  <p className="mt-1.5 pl-4.5 text-xs leading-relaxed text-muted">
                    {signal.explanation}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* ----------------------------------------------------------- coverage */}
      {coverage && <Coverage coverage={coverage} pagesCaptured={pagesCaptured} />}

      {/* ----------------------------------------------------------- findings */}
      {!!notable.length && (
        <Panel title="Notable findings">
          <ul className="space-y-2 text-sm leading-relaxed text-ink-soft">
            {notable.map((item) => (
              <li key={item} className="border-t border-line pt-2 first:border-t-0 first:pt-0">
                {item}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* ----------------------------------------------------------- evidence */}
      {!!evidence.length && (
        <Panel title={`Evidence (${evidence.length})`}>
          <ul className="space-y-3">
            {evidence.slice(0, 12).map((item: Evidence, index) => (
              <li
                key={`${item.source_url ?? "evidence"}-${index}`}
                className="border-t border-line pt-3 first:border-t-0 first:pt-0"
              >
                {item.title && <p className="text-sm font-medium">{item.title}</p>}
                {item.summary && (
                  <p className="mt-1 text-xs leading-relaxed text-muted">{item.summary}</p>
                )}
                {item.source_url && (
                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-1.5 block break-all font-mono text-[11px] text-signal hover:underline"
                  >
                    {item.source_domain || item.source_url}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <p className="px-1 text-xs leading-relaxed text-faint">
        Signals are advisory. Always verify an employer through a channel you initiate yourself, and
        never pay for a job, internship or “training”.
      </p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-paper p-6">
      <h3 className="eyebrow">{title}</h3>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Analyst({ analyst }: { analyst: AnalystReport }) {
  return (
    <Panel title="AI analyst">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-display text-2xl font-semibold text-crimson">
          {analyst.fraud_score ?? 0}
          <span className="text-sm text-faint">/100 fraud</span>
        </span>
        {analyst.verdict && (
          <span className="text-xs capitalize text-muted">
            {analyst.verdict.replace(/_/g, " ")}
          </span>
        )}
      </div>

      {analyst.summary && (
        <p className="mt-3 text-sm leading-relaxed text-ink-soft">{analyst.summary}</p>
      )}

      <Flags title="Red flags" flags={analyst.red_flags} tone="red" />
      <Flags title="Green flags" flags={analyst.green_flags} tone="green" />

      {!!analyst.what_to_do?.length && (
        <div className="mt-4">
          <p className="eyebrow">What to do</p>
          <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-ink-soft">
            {analyst.what_to_do.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {!!analyst.sources_used?.length && (
        <div className="mt-4">
          <p className="eyebrow">Sources</p>
          <ul className="mt-2 space-y-1">
            {analyst.sources_used.slice(0, 10).map((url) => (
              <li key={url}>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="break-all font-mono text-[11px] text-signal hover:underline"
                >
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function Flags({
  title,
  flags,
  tone,
}: {
  title: string;
  flags?: AnalystReport["red_flags"];
  tone: "red" | "green";
}) {
  if (!flags?.length) return null;
  const accent = tone === "red" ? "border-crimson" : "border-moss";
  return (
    <div className="mt-4">
      <p className="eyebrow">{title}</p>
      <ul className="mt-2 space-y-2">
        {flags.map((flag, index) => (
          <li key={`${flag.point}-${index}`} className={cx("border-l-2 pl-3", accent)}>
            <p className="text-sm font-medium">{flag.point}</p>
            {flag.evidence && (
              <p className="mt-1 text-xs leading-relaxed text-muted">{flag.evidence}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Coverage({
  coverage,
  pagesCaptured,
}: {
  coverage: EvidenceCoverage;
  pagesCaptured?: number;
}) {
  const captured = pagesCaptured ?? coverage.pages_captured;
  const stats: Array<[string, number]> = [
    ["Pages captured", captured],
    ["Sent to model", coverage.pages_in_prompt],
    ["Rules-only", coverage.pages_rules_only],
    ["Signal sentences", coverage.signal_sentences],
  ];

  return (
    <Panel title="Evidence coverage">
      <p className="text-xs text-muted">
        {coverage.sufficient ? "Sufficient to judge" : "Insufficient to judge"}
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-line px-3 py-2">
            <div className="font-display text-lg font-semibold">{value ?? 0}</div>
            <div className="eyebrow mt-0.5 text-[10px]">{label}</div>
          </div>
        ))}
      </div>
      {coverage.note && (
        <p className="mt-3 text-xs leading-relaxed text-muted">{coverage.note}</p>
      )}
    </Panel>
  );
}
