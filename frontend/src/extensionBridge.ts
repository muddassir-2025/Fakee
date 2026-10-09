/**
 * Bridge between the landing page and the Fakee browser extension.
 *
 * The page cannot search the web itself (CORS, and search keys must not ship in
 * a public bundle), so the extension does the searching and page reading in the
 * user's own browser and hands the verdict back. That happens over a
 * `chrome.runtime` Port:
 *
 *   page  -> connect(extensionId, "fakee-bridge")
 *   page  -> { type: "PING" }            extension -> { type: "PONG" }
 *   page  -> { type: "RUN", text }       extension -> ...PROGRESS... -> RESULT | ERROR
 *
 * Chrome only lets a page connect to an extension that lists this site in its
 * manifest `externally_connectable`, and the page must know the extension's id.
 */

export type RiskLevel = "LOW" | "MODERATE" | "HIGH" | "CRITICAL";
export type Severity = "info" | "low" | "medium" | "high" | "critical";

export interface RiskSignal {
  id: string;
  label: string;
  category: string;
  severity: Severity;
  weight: number;
  points: number;
  confidence: number;
  explanation: string;
  evidence?: string[];
}

export interface TrustSignal {
  id: string;
  label: string;
  points: number;
  explanation: string;
}

export interface AnalystFlag {
  point: string;
  evidence?: string;
}

export interface AnalystReport {
  fraud_score: number;
  verdict: string;
  summary: string;
  red_flags?: AnalystFlag[];
  green_flags?: AnalystFlag[];
  what_to_do?: string[];
  sources_used?: string[];
}

export interface Evidence {
  type: string;
  source_url?: string | null;
  source_domain?: string | null;
  title?: string | null;
  summary: string;
  relevance: number;
}

export interface EvidenceCoverage {
  pages_captured: number;
  pages_in_prompt: number;
  pages_rules_only: number;
  signal_sentences: number;
  topical: boolean;
  sufficient: boolean;
  note: string;
}

export interface RiskAssessment {
  score: number;
  level: RiskLevel;
  status: string;
  headline: string;
  summary: string;
  recommendation: string;
  confidence: number;
  signals?: RiskSignal[];
  trust_score: number;
  trust_signals?: TrustSignal[];
  verified?: string[];
  unverified?: string[];
  checklist?: string[];
  deadline?: string | null;
  expired?: boolean;
  analyst?: AnalystReport | null;
  evidence?: Evidence[];
  source_urls?: string[];
}

export interface InvestigationResult {
  id: string;
  cached?: boolean;
  investigation?: {
    coverage?: EvidenceCoverage;
    notable_findings?: string[];
    evidence?: Evidence[];
  };
  risk: RiskAssessment;
}

export interface ProgressEvent {
  phase: string;
  done?: number;
  total?: number;
  note?: string;
}

/** Human-readable text for each opportunity status the backend can return. */
export const STATUS_TEXT: Record<string, string> = {
  FRAUDULENT_VERIFIED: "Fraudulent (verified)",
  LEGITIMATE_VERIFIED: "Legitimate (verified)",
  LIKELY_LEGITIMATE_UNVERIFIED: "Likely legitimate (unverified)",
  NEEDS_VERIFICATION: "Needs verification",
  EXPIRED: "Expired — not necessarily fake",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  CONFLICTING_EVIDENCE: "Conflicting evidence",
};

export const PORT_NAME = "fakee-bridge";

/**
 * A Chrome extension id is 32 characters from a–p. Store URLs look like
 * `https://chromewebstore.google.com/detail/<slug>/<id>`, so the id can be read
 * straight out of the listing URL the site is configured with.
 */
export function extensionIdFromStoreUrl(storeUrl: string): string {
  const match = /\/detail\/(?:[^/?#]+\/)?([a-p]{32})(?:[/?#]|$)/.exec(storeUrl || "");
  return match ? match[1] : "";
}

/** The subset of `chrome.runtime` a web page is given. */
interface ChromeRuntimeLike {
  connect?: (extensionId: string, connectInfo?: { name?: string }) => ChromePortLike;
}

interface ChromePortLike {
  postMessage: (message: unknown) => void;
  disconnect: () => void;
  onMessage: { addListener: (listener: (message: any) => void) => void };
  onDisconnect: { addListener: (listener: () => void) => void };
}

function chromeRuntime(): ChromeRuntimeLike | undefined {
  const chromeApi = (globalThis as { chrome?: { runtime?: ChromeRuntimeLike } }).chrome;
  return chromeApi?.runtime;
}

/** True when this browser exposes the external-messaging API at all. */
export function canTalkToExtension(): boolean {
  return typeof chromeRuntime()?.connect === "function";
}

/**
 * Resolve once we know whether the extension is installed and reachable.
 * Never rejects: an unreachable extension is a normal outcome, not an error.
 */
export function detectExtension(extensionId: string, timeoutMs = 1500): Promise<boolean> {
  const runtime = chromeRuntime();
  const connect = runtime?.connect?.bind(runtime);
  if (!extensionId || !connect) return Promise.resolve(false);

  return new Promise<boolean>((resolve) => {
    let settled = false;
    let port: ChromePortLike | undefined;

    const finish = (found: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        port?.disconnect();
      } catch {
        /* already gone */
      }
      resolve(found);
    };

    const timer = setTimeout(() => finish(false), timeoutMs);

    try {
      port = connect(extensionId, { name: PORT_NAME });
    } catch {
      finish(false); // no listener registered: the extension is not installed
      return;
    }

    port.onMessage.addListener((message) => {
      if (message?.type === "PONG") finish(true);
    });
    port.onDisconnect.addListener(() => finish(false));

    try {
      port.postMessage({ type: "PING" });
    } catch {
      finish(false);
    }
  });
}

export interface RunOptions {
  extensionId: string;
  text: string;
  /** Overrides the extension's saved backend URL for this run, if provided. */
  apiBase?: string;
  onProgress?: (event: ProgressEvent) => void;
  signal?: AbortSignal;
}

/**
 * Run one investigation in the extension and resolve with its verdict.
 * Rejects with a user-readable message if the extension is missing or fails.
 */
export function runInvestigation({
  extensionId,
  text,
  apiBase,
  onProgress,
  signal,
}: RunOptions): Promise<InvestigationResult> {
  const runtime = chromeRuntime();
  const connect = runtime?.connect?.bind(runtime);
  if (!extensionId || !connect) {
    return Promise.reject(new Error("The Fakee extension is not available in this browser."));
  }

  return new Promise<InvestigationResult>((resolve, reject) => {
    let port: ChromePortLike;
    let settled = false;

    const cleanup = () => {
      try {
        port?.disconnect();
      } catch {
        /* already gone */
      }
    };
    const succeed = (result: InvestigationResult) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(result);
    };
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };

    try {
      port = connect(extensionId, { name: PORT_NAME });
    } catch {
      fail(new Error("Could not reach the extension. Is it installed and enabled?"));
      return;
    }

    port.onMessage.addListener((message) => {
      if (!message) return;
      if (message.type === "PROGRESS") {
        onProgress?.({
          phase: message.phase,
          done: message.done,
          total: message.total,
          note: message.note,
        });
      } else if (message.type === "RESULT") {
        succeed(message.result as InvestigationResult);
      } else if (message.type === "ERROR") {
        fail(new Error(message.message || "The investigation failed."));
      }
    });

    port.onDisconnect.addListener(() => {
      fail(new Error("The extension stopped responding before finishing."));
    });

    signal?.addEventListener("abort", () => fail(new Error("Cancelled.")), { once: true });

    const settings = apiBase ? { apiBase } : {};
    port.postMessage({ type: "RUN", text, settings });
  });
}
