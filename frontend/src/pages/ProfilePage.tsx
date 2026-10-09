/**
 * Profile — everything the signed-in user has reported, and its review state.
 *
 * Reports are attributed to an account so a person can see what happened to
 * their submission and take it back if they change their mind ("revert").
 */
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchMyReports,
  withdrawReport,
  type MyReports,
  type ReportOut,
  type ReportStatus,
} from "../api";
import { formatDateTime, relativeTime } from "../format";
import { useAuth } from "../useAuth";
import { GoogleMark, Notice, XIcon } from "../components/authUi";

const STATUS_STYLE: Record<ReportStatus, { label: string; className: string }> = {
  pending: { label: "In review", className: "border-amber/40 bg-amber/10 text-amber" },
  approved: { label: "Accepted", className: "border-moss/40 bg-moss/10 text-moss" },
  rejected: { label: "Rejected", className: "border-crimson/40 bg-crimson/10 text-crimson" },
  withdrawn: { label: "Withdrawn", className: "border-line-strong bg-paper-deep text-muted" },
};

const STATUS_ORDER: ReportStatus[] = ["pending", "approved", "rejected", "withdrawn"];

export function ProfilePage({ onSignInRequest }: { onSignInRequest: () => void }) {
  const { user, loading, available, admin, handedOff, error: authError, refresh, signOut } =
    useAuth();
  const [data, setData] = useState<MyReports | null>(null);
  const [loadingReports, setLoadingReports] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadingReports(true);
    setError(null);
    try {
      setData(await fetchMyReports());
    } catch (err) {
      if (err instanceof ApiError && err.needsSignIn) {
        // Ask the auth client once more (the session may have just expired),
        // but show the outcome rather than silently retrying forever.
        setData(null);
        setError("Your sign-in has expired. Sign in again, then reload your reports.");
        await refresh();
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoadingReports(false);
    }
  }, [refresh]);

  // Keyed on the user *id*: `readSession()` returns a fresh object every check,
  // so depending on the object would re-fetch on every focus event.
  const userId = user?.id ?? null;
  useEffect(() => {
    if (userId) void load();
    else setData(null);
  }, [userId, load]);

  async function onWithdraw(report: ReportOut) {
    setBusy(report.id);
    setNotice(null);
    setError(null);
    try {
      const result = await withdrawReport(report.id);
      setNotice(result.detail);
      setConfirming(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border-b border-line">
      <div className="mx-auto w-full max-w-4xl px-6 py-14 sm:px-8 sm:py-20">
        <p className="eyebrow">Your account</p>
        <h1 className="mt-4 font-display text-3xl font-semibold leading-tight sm:text-4xl">
          My reports
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-muted">
          Every posting you report is filed against an account so you can follow it — and withdraw
          it — instead of it disappearing into a queue.
        </p>

        {!available && (
          <Notice tone="warn" title="Sign-in is not configured for this deployment.">
            Set <code className="font-mono">VITE_NEON_AUTH_URL</code> (frontend) and{" "}
            <code className="font-mono">NEON_AUTH_BASE_URL</code> (backend) to the same Neon Auth
            URL. Until then the API refuses reports rather than storing them unattributed.
          </Notice>
        )}

        {available && loading && (
          <p className="mt-10 text-sm text-muted" role="status">
            Checking your session…
          </p>
        )}

        {available && !loading && !user && (
          <SignInCard onSignInRequest={onSignInRequest} error={authError} />
        )}

        {user && (
          <>
            <div className="mt-10 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-line bg-paper p-5">
              <div className="flex items-center gap-3">
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-signal-soft font-display text-base font-semibold text-signal">
                  {(user.name || user.email || "?").slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <p className="text-sm font-semibold">{user.name || "Signed in"}</p>
                  <p className="text-xs text-muted">{user.email}</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {admin && (
                  <a
                    href="#/admin"
                    className="rounded-full border border-line-strong px-4 py-2 text-xs font-semibold text-ink transition-colors hover:bg-paper-deep"
                  >
                    Admin dashboard
                  </a>
                )}
                <button
                  type="button"
                  onClick={load}
                  disabled={loadingReports}
                  className="rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-ink transition-colors hover:bg-paper-deep disabled:opacity-50"
                >
                  {loadingReports ? "Refreshing…" : "Refresh"}
                </button>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-muted transition-colors hover:bg-paper-deep hover:text-ink"
                >
                  Sign out
                </button>
              </div>
            </div>

            {handedOff && (
              <Notice tone="good" title="Signed in for the extension too.">
                You can close this tab and use <strong>Report as scam</strong> in the side panel.
              </Notice>
            )}

            {notice && (
              <Notice tone="good" title="Done">
                {notice}
              </Notice>
            )}
            {error && (
              <Notice tone="bad" title="Something went wrong">
                {error}
              </Notice>
            )}

            <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {STATUS_ORDER.map((status) => (
                <div key={status} className="rounded-xl border border-line bg-paper px-4 py-3">
                  <div className="font-display text-2xl font-semibold">
                    {data?.counts?.[status] ?? 0}
                  </div>
                  <div className="eyebrow mt-1">{STATUS_STYLE[status].label}</div>
                </div>
              ))}
            </div>

            {loadingReports && !data && (
              <p className="mt-8 text-sm text-muted" role="status">
                Loading your reports…
              </p>
            )}

            {data && data.reports.length === 0 && (
              <div className="mt-8 rounded-2xl border border-line bg-paper-deep/40 p-6">
                <p className="text-sm font-semibold">You have not reported anything yet.</p>
                <p className="mt-2 text-sm leading-relaxed text-muted">
                  Investigate a posting, then use <strong>Report as scam</strong> in the extension's
                  side panel — or on the result here — to add it to the shared record.
                </p>
                <a
                  href="#/"
                  className="mt-4 inline-flex rounded-full bg-signal px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-signal/90"
                >
                  Check a posting
                </a>
              </div>
            )}

            <ul className="mt-8 space-y-4">
              {data?.reports.map((report) => (
                <ReportRow
                  key={report.id}
                  report={report}
                  busy={busy === report.id}
                  confirming={confirming === report.id}
                  onAskWithdraw={() => {
                    setConfirming(report.id);
                    setNotice(null);
                  }}
                  onCancel={() => setConfirming(null)}
                  onWithdraw={() => onWithdraw(report)}
                />
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

function ReportRow({
  report,
  busy,
  confirming,
  onAskWithdraw,
  onCancel,
  onWithdraw,
}: {
  report: ReportOut;
  busy: boolean;
  confirming: boolean;
  onAskWithdraw: () => void;
  onCancel: () => void;
  onWithdraw: () => void;
}) {
  const style = STATUS_STYLE[report.status] ?? STATUS_STYLE.pending;
  const withdrawable = report.status === "pending" || report.status === "approved";

  return (
    <li className="rounded-2xl border border-line bg-paper p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-display text-lg font-semibold">
            {report.company_name || "Unnamed company"}
          </p>
          <p className="mt-1 text-xs text-muted">
            {formatDateTime(report.created_at)}
            {relativeTime(report.created_at) && ` · ${relativeTime(report.created_at)}`}
            {report.risk_level ? ` · flagged ${report.risk_level}` : ""}
            {report.risk_score != null ? ` (${report.risk_score}/100)` : ""}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wide ${style.className}`}
        >
          {style.label}
        </span>
      </div>

      <p className="mt-4 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-line bg-paper-deep/40 p-3 text-sm leading-relaxed text-ink-soft">
        {report.description}
      </p>

      {report.review_note && (
        <p className="mt-3 text-xs leading-relaxed text-muted">
          <span className="eyebrow mr-2">
            {report.status === "withdrawn" ? "Note" : "Reviewer note"}
          </span>
          {report.review_note}
          {report.reviewed_at ? ` · ${formatDateTime(report.reviewed_at)}` : ""}
        </p>
      )}

      {withdrawable && (
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          {confirming ? (
            <>
              <p className="text-xs text-muted">
                Withdraw this report? It stops counting towards this company's history.
              </p>
              <button
                type="button"
                onClick={onWithdraw}
                disabled={busy}
                className="rounded-full bg-crimson px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {busy ? "Withdrawing…" : "Yes, withdraw"}
              </button>
              <button
                type="button"
                onClick={onCancel}
                disabled={busy}
                className="rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-ink transition-colors hover:bg-paper-deep disabled:opacity-60"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={onAskWithdraw}
              className="rounded-full border border-line-strong px-4 py-2 text-xs font-medium text-ink transition-colors hover:bg-paper-deep"
            >
              Withdraw report
            </button>
          )}
        </div>
      )}

      {report.status === "rejected" && (
        <p className="mt-4 border-t border-line pt-4 text-xs text-muted">
          An administrator reviewed this report and did not accept it. It no longer counts towards
          the company's history.
        </p>
      )}
    </li>
  );
}

function SignInCard({
  onSignInRequest,
  error,
}: {
  onSignInRequest: () => void;
  error: string | null;
}) {
  return (
    <div className="mt-10 rounded-2xl border border-line bg-paper p-6 sm:p-8">
      <span className="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong">
        <XIcon className="h-5 w-5 text-signal" />
      </span>
      <h2 className="mt-5 font-display text-xl font-semibold">Sign in to see your reports</h2>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
        Reporting a scam and following its outcome both need an account, so a report can be
        attributed and withdrawn by the person who filed it. Investigating an posting stays free and
        anonymous — no account, and nothing you paste is stored.
      </p>
      <p className="mt-3 max-w-xl text-xs leading-relaxed text-faint">
        Signing in creates a Neon Auth account with your Google address. We store only that address,
        your name and the reports you file — see the{" "}
        <a href="/privacy" className="underline underline-offset-4">
          privacy policy
        </a>
        .
      </p>
      <button
        type="button"
        onClick={onSignInRequest}
        className="mt-6 inline-flex items-center gap-3 rounded-full bg-ink px-6 py-3 text-sm font-semibold text-paper transition-colors hover:bg-ink-soft"
      >
        <GoogleMark />
        Continue with Google
      </button>
      {error && (
        <p role="alert" className="mt-4 text-sm text-crimson">
          {error}
        </p>
      )}
    </div>
  );
}
