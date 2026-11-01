/**
 * The website's own path to a verdict.
 *
 * The site calls the backend directly — no browser extension, no
 * `chrome.runtime`, no client-to-client messaging — and the backend owns the
 * whole search: it plans the queries, runs them with the deployment's own Brave
 * Search key, reads the pages, structures the evidence and scores it.
 *
 * The extension is the optional alternative: a visitor who would rather search
 * with their own key runs the same investigation from the side panel instead.
 */
import { ApiError, apiRequest } from "./api";
import type { InvestigationResult, ProgressEvent } from "./extensionBridge";

export interface RunInvestigationOptions {
  text: string;
  onProgress?: (event: ProgressEvent) => void;
}

/**
 * A failed check.
 *
 * `useOwnKey` marks the failures the extension can actually solve — the site's
 * shared search key is missing, rejected, or out of allowance — so the UI can
 * offer that route instead of only printing an error.
 */
export class InvestigationError extends Error {
  useOwnKey: boolean;

  constructor(message: string, useOwnKey = false) {
    super(message);
    this.name = "InvestigationError";
    this.useOwnKey = useOwnKey;
  }
}

/** Provider failures a visitor's own key (in the extension) would get past. */
const OWN_KEY_KINDS = new Set(["missing_key", "invalid_key", "quota_exhausted"]);

const OWN_KEY_MESSAGE =
  "The site's shared search key is unavailable right now, so this check could not run. " +
  "Install the extension and add your own Brave Search API key to run it there.";

/**
 * Run one investigation server-side and resolve with the verdict.
 *
 * Progress is reported at the phase boundaries the server exposes (it returns a
 * single response, so there is no token-by-token stream); the phases still give
 * the user a sense of what is happening rather than a frozen button.
 */
export async function investigate({
  text,
  onProgress,
}: RunInvestigationOptions): Promise<InvestigationResult> {
  const trimmed = text.trim();
  if (trimmed.length < 12) {
    throw new InvestigationError("Please paste a little more of the posting first.");
  }

  onProgress?.({ phase: "Extracting the posting" });
  onProgress?.({ phase: "Searching and reading pages" });

  try {
    const result = await apiRequest<InvestigationResult>("/api/investigate", {
      method: "POST",
      body: {
        text: trimmed,
        persist: false,
        // Server-side search with the deployment's own key; the visitor is never
        // asked for one. "brave" is explicit so an unusable key is an error we
        // can explain, rather than a silent fall back to demo data.
        search_provider: "brave",
      },
    });
    onProgress?.({ phase: "Scoring the evidence" });
    return result;
  } catch (err) {
    throw humanizeError(err);
  }
}

function humanizeError(err: unknown): InvestigationError {
  if (err instanceof ApiError) {
    if (err.status === 0) {
      return new InvestigationError(
        "Could not reach the API. Check your connection and try again.",
      );
    }
    if (err.status === 422) {
      return new InvestigationError("The posting is too short to investigate.");
    }
    if (OWN_KEY_KINDS.has(err.kind)) {
      return new InvestigationError(OWN_KEY_MESSAGE, true);
    }
    return new InvestigationError(err.message);
  }
  return new InvestigationError(err instanceof Error ? err.message : String(err));
}
