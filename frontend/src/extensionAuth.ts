/**
 * Hand the signed-in session to the Fakee extension.
 *
 * The extension cannot run an own OAuth flow comfortably, so it opens this site
 * with `?ext=<its own id>`; once the user signs in here, the page pushes the
 * freshly minted JWT to the extension over `chrome.runtime.sendMessage`. The
 * extension stores it and attaches it as a bearer token when posting reports.
 *
 * Chrome only allows this when the extension lists this site in
 * `externally_connectable`, and only for a signed-in user — this never runs for
 * an anonymous visitor.
 */
import type { AuthUser } from "./auth";

interface ChromeRuntimeLike {
  sendMessage?: (
    extensionId: string,
    message: unknown,
    callback?: (response?: unknown) => void,
  ) => void;
  lastError?: { message?: string };
}

function chromeRuntime(): ChromeRuntimeLike | undefined {
  return (globalThis as { chrome?: { runtime?: ChromeRuntimeLike } }).chrome?.runtime;
}

/** The extension id the page was opened with, if any. */
export function requestedExtensionId(search = window.location.search): string | null {
  const value = new URLSearchParams(search).get("ext");
  if (!value) return null;
  // Chrome extension ids are 32 characters from a–p. Anything else is junk and
  // is not worth passing to chrome.runtime.
  return /^[a-p]{32}$/.test(value) ? value : null;
}

export interface HandoffPayload {
  type: "AUTH_SESSION";
  token: string;
  user: { id: string; email: string | null; name: string | null };
}

/**
 * Send the session to the extension. Resolves false when the extension is not
 * installed, not listening, or rejected the message — all normal outcomes.
 */
export function handOffSession(
  extensionId: string,
  token: string,
  user: AuthUser,
): Promise<boolean> {
  const runtime = chromeRuntime();
  if (!runtime?.sendMessage) return Promise.resolve(false);

  const message: HandoffPayload = {
    type: "AUTH_SESSION",
    token,
    user: { id: user.id, email: user.email ?? null, name: user.name ?? null },
  };

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const settle = (value: boolean) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    // A callback that never fires (no listener) would hang the page's promise.
    const timer = setTimeout(() => settle(false), 2500);
    try {
      runtime.sendMessage?.(extensionId, message, (response?: unknown) => {
        clearTimeout(timer);
        const error = runtime.lastError;
        settle(Boolean(!error && (response as { ok?: boolean } | undefined)?.ok));
      });
    } catch {
      clearTimeout(timer);
      settle(false);
    }
  });
}
