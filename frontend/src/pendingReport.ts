/**
 * Survive the sign-in redirect.
 *
 * Starting Google sign-in navigates away and the page reloads, so a report the
 * user asked to file as an anonymous visitor is parked in sessionStorage and
 * filed as soon as the session exists — instead of asking them to paste and
 * investigate the same posting twice.
 *
 * sessionStorage (not localStorage) on purpose: it is per-tab and disappears
 * when the tab closes, so nothing lingers on a shared machine.
 */
const KEY = "fakee.pendingReport";

export interface PendingReport {
  text: string;
  reportType?: string;
  source?: string;
  companyName?: string | null;
  riskLevel?: string | null;
  riskScore?: number | null;
}

export function savePendingReport(report: PendingReport): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(report));
  } catch {
    /* private mode / storage disabled: the user just signs in and re-reports */
  }
}

export function peekPendingReport(): PendingReport | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingReport;
    return parsed?.text ? parsed : null;
  } catch {
    return null;
  }
}

/** Read and clear in one step, so a report is never filed twice. */
export function takePendingReport(): PendingReport | null {
  const report = peekPendingReport();
  clearPendingReport();
  return report;
}

export function clearPendingReport(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
