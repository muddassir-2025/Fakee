/**
 * "Report as scam" for a verdict shown on the website.
 *
 * Reporting is a write about a named business, so it needs a signed-in user. An
 * anonymous visitor who clicks it is taken through sign-in with the report
 * parked in sessionStorage and filed immediately afterwards (see
 * `pendingReport.ts`), rather than losing what they pasted.
 */
import { useEffect, useState } from "react";
import { ApiError, submitReport } from "../api";
import { savePendingReport, takePendingReport } from "../pendingReport";
import { useAuth } from "../useAuth";

export interface ReportActionProps {
  text: string;
  companyName?: string | null;
  riskLevel?: string | null;
  riskScore?: number | null;
}

export function ReportAction({ text, companyName, riskLevel, riskScore }: ReportActionProps) {
  const { user, available, signIn } = useAuth();
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  // A report parked before signing in is filed as soon as the session arrives.
  useEffect(() => {
    if (!user) return;
    const parked = takePendingReport();
    if (!parked) return;
    let cancelled = false;
    setState("sending");
    void (async () => {
      try {
        await submitReport(parked);
        if (!cancelled) {
          setState("sent");
          setMessage("Your saved report was filed after signing in. Track it under My reports.");
        }
      } catch (err) {
        if (!cancelled) {
          setState("error");
          setMessage(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  async function report() {
    setMessage(null);
    const payload: ReportActionProps = { text, companyName, riskLevel, riskScore };

    if (!user) {
      if (!available) {
        setState("error");
        setMessage("Sign-in is not configured for this deployment, so reports are disabled.");
        return;
      }
      savePendingReport({
        text,
        reportType: "scam",
        source: "web_user",
        companyName: companyName ?? null,
        riskLevel: riskLevel ?? null,
        riskScore: riskScore ?? null,
      });
      setState("sending");
      setMessage("Sign in and this report will be filed automatically.");
      await signIn();
      return;
    }

    setState("sending");
    try {
      await submitReport({
        text: payload.text,
        reportType: "scam",
        source: "web_user",
        companyName: payload.companyName ?? null,
        riskLevel: payload.riskLevel ?? null,
        riskScore: payload.riskScore ?? null,
      });
      setState("sent");
      setMessage("Stored against this company. Track or withdraw it under My reports.");
    } catch (err) {
      setState("error");
      if (err instanceof ApiError && err.needsSignIn) {
        setMessage("Your session expired. Sign in again to file this report.");
      } else {
        setMessage(err instanceof Error ? err.message : String(err));
      }
    }
  }

  return (
    <section className="rounded-2xl border border-line bg-paper p-6">
      <p className="eyebrow">Spotted a scam?</p>
      <p className="mt-3 text-sm leading-relaxed text-muted">
        Reporting it stores it against this company, so repeat offenders surface for everyone. You
        must be signed in to report — that is what lets you see and withdraw it later.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void report()}
          disabled={state === "sending" || state === "sent"}
          className="rounded-full bg-signal px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-signal/90 disabled:opacity-60"
        >
          {state === "sending"
            ? "Sending…"
            : state === "sent"
              ? "Reported ✓"
              : user
                ? "Report as scam"
                : "Sign in to report"}
        </button>
        {user && <span className="text-xs text-muted">Signed in as {user.email}</span>}
      </div>
      {message && (
        <p
          role="status"
          className={`mt-3 text-sm ${state === "error" ? "text-crimson" : "text-muted"}`}
        >
          {message}
        </p>
      )}
    </section>
  );
}
