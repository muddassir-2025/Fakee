/**
 * Admin dashboard — the review queue.
 *
 * Restricted to the addresses in the backend's ADMIN_EMAILS (the API enforces it
 * with its own 403; this page only decides what to render). Shows who filed each
 * report, what they saw, and lets an admin accept, reject or re-queue it.
 */
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchAdminOverview,
  fetchAdminReports,
  reviewReport,
  type AdminOverview,
  type AdminReportOut,
  type ReportStatus,
} from "../api";
import { formatDateTime, relativeTime } from "../format";
import { useAuth } from "../useAuth";
import { GoogleMark, Notice, XIcon } from "../components/authUi";

type Filter = ReportStatus | "all";

const FILTERS: Array<{ value: Filter; label: string }> = [
  { value: "pending", label: "In review" },
  { value: "approved", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "all", label: "All" },
];

const STATUS_STYLE: Record<ReportStatus, string> = {
  pending: "border-amber/40 bg-amber/10 text-amber",
  approved: "border-moss/40 bg-moss/10 text-moss",
  rejected: "border-crimson/40 bg-crimson/10 text-crimson",
  withdrawn: "border-line-strong bg-paper-deep text-muted",
};

export function AdminPage({ onSignInRequest }: { onSignInRequest: () => void }) {
  const { user, loading, available, admin, error: authError } = useAuth();
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [reports, setReports] = useState<AdminReportOut[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<Filter>("pending");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [nextOverview, nextReports] = await Promise.all([
        fetchAdminOverview(),
        fetchAdminReports({ status: filter, q: appliedQuery, limit: 50 }),
      ]);
      setOverview(nextOverview);
      setReports(nextReports.reports);
      setTotal(nextReports.total);
    } catch (err) {
      if (err instanceof ApiError && err.needsSignIn) {
        setError("Your session has expired. Sign in again.");
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  }, [filter, appliedQuery]);

  useEffect(() => {
    if (admin) void load();
  }, [admin, load]);

  async function decide(report: AdminReportOut, action: "approve" | "reject" | "reset", note: string) {
    setNotice(null);
    setError(null);
    try {
      const result = await reviewReport(report.id, action, note);
      setNotice(result.detail);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!available) {
    return (
      <Shell title="Admin">
        <Notice tone="warn" title="Sign-in is not configured for this deployment.">
          Set <code className="font-mono">VITE_NEON_AUTH_URL</code> so an administrator can sign in.
        </Notice>
      </Shell>
    );
  }

  if (loading) {
    return (
      <Shell title="Admin">
        <p className="mt-8 text-sm text-muted" role="status">
          Checking your session…
        </p>
      </Shell>
    );
  }

  if (!user) {
    return (
      <Shell title="Admin">
        <div className="mt-8 rounded-2xl border border-line bg-paper p-6 sm:p-8">
          <span className="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong">
            <XIcon className="h-5 w-5 text-signal" />
          </span>
          <h2 className="mt-5 font-display text-xl font-semibold">Administrators only</h2>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
            Sign in with the administrator account to review submitted reports.
          </p>
          <button
            type="button"
            onClick={onSignInRequest}
            className="mt-6 inline-flex items-center gap-3 rounded-full bg-ink px-6 py-3 text-sm font-semibold text-paper transition-colors hover:bg-ink-soft"
          >
            <GoogleMark />
            Continue with Google
          </button>
          {authError && (
            <p role="alert" className="mt-4 text-sm text-crimson">
              {authError}
            </p>
          )}
        </div>
      </Shell>
    );
  }

  if (!admin) {
    return (
      <Shell title="Admin">
        <Notice tone="bad" title="This area is restricted to administrators.">
          {user.email} is not on the administrator list. If that is wrong, add the address to{" "}
          <code className="font-mono">ADMIN_EMAILS</code> on the backend.
        </Notice>
        <a
          href="#/profile"
          className="mt-6 inline-flex rounded-full border border-line-strong px-5 py-2.5 text-sm font-medium text-ink transition-colors hover:bg-paper-deep"
        >
          Go to my reports
        </a>
      </Shell>
    );
  }

  return (
    <Shell title="Admin dashboard">
      {error && (
        <Notice tone="bad" title="Something went wrong">
          {error}
        </Notice>
      )}
      {notice && (
        <Notice tone="good" title="Done">
          {notice}
        </Notice>
      )}

      {/* ------------------------------------------------------------ totals */}
      <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="In review" value={overview?.reports?.pending ?? 0} tone="amber" />
        <Stat label="Accepted" value={overview?.reports?.approved ?? 0} tone="moss" />
        <Stat label="Rejected" value={overview?.reports?.rejected ?? 0} tone="crimson" />
        <Stat label="Withdrawn" value={overview?.reports?.withdrawn ?? 0} />
        <Stat label="Reporters" value={overview?.users_reporting ?? 0} />
        <Stat label="Last 7 days" value={overview?.reports_last_7_days ?? 0} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="rounded-2xl border border-line bg-paper p-5">
          <p className="eyebrow">Most reported companies</p>
          {overview?.top_companies?.length ? (
            <ul className="mt-3 space-y-2">
              {overview.top_companies.map((row) => (
                <li key={row.company} className="flex items-baseline justify-between gap-4 text-sm">
                  <span className="min-w-0 truncate">{row.company}</span>
                  <span className="shrink-0 font-mono text-xs text-muted">{row.reports}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted">No reports yet.</p>
          )}
        </div>
        <div className="rounded-2xl border border-line bg-paper p-5">
          <p className="eyebrow">System</p>
          <dl className="mt-3 space-y-2 text-sm">
            <Row label="Investigations run" value={String(overview?.investigations ?? 0)} />
            <Row label="Companies known" value={String(overview?.companies ?? 0)} />
            <Row label="Admins" value={(overview?.admins ?? []).join(", ") || "—"} />
            <Row label="Sign-in configured" value={overview?.auth_configured ? "yes" : "no"} />
          </dl>
        </div>
      </div>

      {/* ------------------------------------------------------------ queue */}
      <div className="mt-10 flex flex-wrap items-center gap-2">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setFilter(item.value)}
            className={
              filter === item.value
                ? "rounded-full bg-ink px-4 py-2 text-xs font-semibold text-paper"
                : "rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-ink transition-colors hover:bg-paper-deep"
            }
          >
            {item.label}
            {overview?.reports?.[item.value] != null && (
              <span className="ml-2 font-mono text-[11px] opacity-70">
                {overview.reports[item.value]}
              </span>
            )}
          </button>
        ))}

        <form
          className="ml-auto flex w-full items-center gap-2 sm:w-auto"
          onSubmit={(event) => {
            event.preventDefault();
            setAppliedQuery(query);
          }}
        >
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search company, text or reporter"
            aria-label="Search reports"
            className="w-full min-w-0 rounded-full border border-line bg-paper px-4 py-2 text-sm placeholder:text-faint focus:border-signal focus:outline-none sm:w-72"
          />
          <button
            type="submit"
            className="shrink-0 rounded-full border border-line-strong px-4 py-2 text-xs font-semibold text-ink transition-colors hover:bg-paper-deep"
          >
            Search
          </button>
        </form>
      </div>

      <p className="mt-4 text-xs text-muted">
        {busy ? "Loading…" : `${reports.length} of ${total} report${total === 1 ? "" : "s"} shown`}
      </p>

      {!busy && reports.length === 0 && (
        <p className="mt-6 rounded-2xl border border-line bg-paper-deep/40 p-6 text-sm text-muted">
          Nothing here for this filter.
        </p>
      )}

      <ul className="mt-4 space-y-4">
        {reports.map((report) => (
          <AdminReportCard key={report.id} report={report} onDecide={decide} />
        ))}
      </ul>
    </Shell>
  );
}

function AdminReportCard({
  report,
  onDecide,
}: {
  report: AdminReportOut;
  onDecide: (report: AdminReportOut, action: "approve" | "reject" | "reset", note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [working, setWorking] = useState<null | "approve" | "reject" | "reset">(null);

  const act = async (action: "approve" | "reject" | "reset") => {
    setWorking(action);
    try {
      await onDecide(report, action, note);
    } finally {
      setWorking(null);
    }
  };

  return (
    <li className="rounded-2xl border border-line bg-paper p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-display text-lg font-semibold">
            {report.company_name || "Unnamed company"}
          </p>
          <p className="mt-1 text-xs text-muted">
            Filed {formatDateTime(report.created_at)}
            {relativeTime(report.created_at) && ` · ${relativeTime(report.created_at)}`}
            {report.risk_level
              ? ` · verdict ${report.risk_level}${report.risk_score != null ? ` (${report.risk_score}/100)` : ""}`
              : ""}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wide ${STATUS_STYLE[report.status]}`}
        >
          {report.status}
        </span>
      </div>

      {/* Who filed it — the point of the dashboard. */}
      <dl className="mt-4 grid gap-x-6 gap-y-2 rounded-xl border border-line bg-paper-deep/40 p-3 text-xs sm:grid-cols-2">
        <Row label="Reporter" value={report.user_name || "—"} />
        <Row label="Email" value={report.user_email || "—"} mono />
        <Row label="Account id" value={report.user_id || "— (pre-sign-in report)"} mono />
        <Row label="Source" value={report.source} />
      </dl>

      <p className="mt-4 max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-line p-3 text-sm leading-relaxed text-ink-soft">
        {report.description}
      </p>

      {(report.review_note || report.reviewed_by) && (
        <p className="mt-3 text-xs leading-relaxed text-muted">
          <span className="eyebrow mr-2">Review</span>
          {report.review_note}
          {report.reviewed_by ? ` — ${report.reviewed_by}` : ""}
          {report.reviewed_at ? ` · ${formatDateTime(report.reviewed_at)}` : ""}
        </p>
      )}

      {report.status === "withdrawn" ? (
        <p className="mt-4 border-t border-line pt-4 text-xs text-muted">
          The reporter withdrew this report; it no longer counts and cannot be re-reviewed.
        </p>
      ) : (
        <div className="mt-4 border-t border-line pt-4">
          <label htmlFor={`note-${report.id}`} className="eyebrow">
            Decision note (optional)
          </label>
          <input
            id={`note-${report.id}`}
            type="text"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why this decision — shown to the reporter"
            className="mt-2 w-full rounded-xl border border-line bg-paper px-3 py-2 text-sm placeholder:text-faint focus:border-signal focus:outline-none"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void act("approve")}
              disabled={working !== null}
              className="rounded-full bg-moss px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {working === "approve" ? "Accepting…" : "Accept"}
            </button>
            <button
              type="button"
              onClick={() => void act("reject")}
              disabled={working !== null}
              className="rounded-full bg-crimson px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {working === "reject" ? "Rejecting…" : "Reject"}
            </button>
            <button
              type="button"
              onClick={() => void act("reset")}
              disabled={working !== null || report.status === "pending"}
              className="rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-ink transition-colors hover:bg-paper-deep disabled:opacity-50"
            >
              {working === "reset" ? "Re-queuing…" : "Back to review"}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-line">
      <div className="mx-auto w-full max-w-5xl px-6 py-14 sm:px-8 sm:py-20">
        <p className="eyebrow">Restricted</p>
        <h1 className="mt-4 font-display text-3xl font-semibold leading-tight sm:text-4xl">
          {title}
        </h1>
        {children}
      </div>
    </section>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "amber" | "moss" | "crimson" }) {
  const colour = tone === "amber" ? "text-amber" : tone === "moss" ? "text-moss" : tone === "crimson" ? "text-crimson" : "";
  return (
    <div className="rounded-xl border border-line bg-paper px-4 py-3">
      <div className={`font-display text-2xl font-semibold ${colour}`}>{value}</div>
      <div className="eyebrow mt-1">{label}</div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className={`min-w-0 truncate text-right ${mono ? "font-mono text-[11px]" : ""}`}>{value}</dd>
    </div>
  );
}
