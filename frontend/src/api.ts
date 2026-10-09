/**
 * Typed client for the Fakee API (profile + admin + reporting).
 *
 * The site talks to the backend directly; in development Vite proxies `/api` to
 * the local server, and in a deployment `VITE_API_BASE` points at the API origin
 * (add that origin to the backend's CORS_ORIGINS).
 */
import { accessToken } from "./auth";

const API_BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").replace(/\/+$/, "");

export type ReportStatus = "pending" | "approved" | "rejected" | "withdrawn";

export interface ReportOut {
  id: string;
  created_at: string;
  updated_at: string | null;
  status: ReportStatus;
  report_type: string;
  source: string;
  company_id: string | null;
  company_name: string | null;
  description: string;
  risk_level: string | null;
  risk_score: number | null;
  review_note: string | null;
  reviewed_at: string | null;
}

export interface AdminReportOut extends ReportOut {
  user_id: string | null;
  user_email: string | null;
  user_name: string | null;
  reviewed_by: string | null;
}

export interface ProfileUser {
  id: string;
  email: string | null;
  name: string | null;
  email_verified: boolean;
  is_admin: boolean;
}

export interface MyReports {
  user: ProfileUser;
  reports: ReportOut[];
  counts: Record<string, number>;
}

export interface AdminReports {
  reports: AdminReportOut[];
  counts: Record<string, number>;
  total: number;
  limit: number;
  offset: number;
}

export interface AdminOverview {
  reports: Record<string, number>;
  investigations: number;
  companies: number;
  users_reporting: number;
  reports_last_7_days: number;
  by_risk_level: Record<string, number>;
  top_companies: Array<{ company: string; reports: number }>;
  admins: string[];
  auth_configured: boolean;
}

/** An API failure with enough detail for the UI and for tests to assert on. */
export class ApiError extends Error {
  status: number;
  /** True when the caller should be asked to sign in (or sign in again). */
  needsSignIn: boolean;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.needsSignIn = status === 401;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  /** Send the bearer token (required by every write endpoint). */
  auth?: boolean;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = false } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (auth) {
    const token = await accessToken();
    if (!token) {
      throw new ApiError(401, "Sign in to continue.");
    }
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Could not reach the API. Check the connection and try again.");
  }

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : `Request failed (${response.status}).`,
    );
  }
  return payload as T;
}

// --------------------------------------------------------------- endpoints ---

export interface SubmitReport {
  text: string;
  reportType?: string;
  source?: string;
  companyName?: string | null;
  riskLevel?: string | null;
  riskScore?: number | null;
}

/** File a scam report. Requires a signed-in user; throws ApiError(401) if not. */
export function submitReport(input: SubmitReport): Promise<ReportOut> {
  return apiRequest<ReportOut>("/api/reports", {
    method: "POST",
    auth: true,
    body: {
      text: input.text,
      report_type: input.reportType ?? "scam",
      source: input.source ?? "web_user",
      company_name: input.companyName ?? null,
      risk_level: input.riskLevel ?? null,
      risk_score: input.riskScore ?? null,
    },
  });
}

export function fetchMyReports(): Promise<MyReports> {
  return apiRequest<MyReports>("/api/reports/mine", { auth: true });
}

export function withdrawReport(id: string): Promise<{ id: string; status: ReportStatus; detail: string }> {
  return apiRequest(`/api/reports/${encodeURIComponent(id)}/withdraw`, {
    method: "POST",
    auth: true,
  });
}

export function fetchAdminOverview(): Promise<AdminOverview> {
  return apiRequest<AdminOverview>("/api/admin/overview", { auth: true });
}

export function fetchAdminReports(params: {
  status?: ReportStatus | "all";
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<AdminReports> {
  const search = new URLSearchParams();
  if (params.status && params.status !== "all") search.set("status", params.status);
  if (params.q?.trim()) search.set("q", params.q.trim());
  search.set("limit", String(params.limit ?? 50));
  search.set("offset", String(params.offset ?? 0));
  return apiRequest<AdminReports>(`/api/admin/reports?${search}`, { auth: true });
}

export function reviewReport(
  id: string,
  action: "approve" | "reject" | "reset",
  note?: string,
): Promise<{ id: string; status: ReportStatus; detail: string; reviewed_at: string | null }> {
  return apiRequest(`/api/admin/reports/${encodeURIComponent(id)}/review`, {
    method: "POST",
    auth: true,
    body: { action, note: note?.trim() ? note.trim() : null },
  });
}

/** The backend base URL, for messages that need to name it. */
export const apiBase = API_BASE || "(same origin)";
