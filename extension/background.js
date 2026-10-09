// MV3 service worker.
//
// The service worker is short-lived (Chrome terminates it after ~30s idle), so
// it does not run the crawl itself. It only:
//   * opens the side panel on toolbar click,
//   * ensures the offscreen document exists and forwards "RUN" requests to it,
//   * mirrors crawl progress/results to any web page connected to it, so the
//     website can render a verdict inline (see `externally_connectable`).
// The crawl runs in the offscreen document, which has a DOM (DOMParser) and is
// not subject to the service-worker idle timeout.

const OFFSCREEN_PATH = "offscreen.html";

// Web pages allowed to drive the extension. Chrome already enforces this via
// the manifest's `externally_connectable` list; re-checking the origin here is
// defence in depth, because this is a security tool. Keep in sync with the
// manifest when you add a custom domain.
const ALLOWED_EXTERNAL_ORIGIN =
  /^(https:\/\/[a-z0-9-]+(\.[a-z0-9-]+)*\.vercel\.app|http:\/\/localhost(:\d+)?)$/i;

// Ports opened by web pages (chrome.runtime.connect from the site).
const externalPorts = new Set();

async function hasOffscreenDocument() {
  if (!chrome.runtime.getContexts) return false;
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_PATH)],
  });
  return contexts.length > 0;
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) return;
  try {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ["DOM_PARSER"],
      justification:
        "Parse search-result HTML and extract readable page text for job-scam investigation.",
    });
  } catch (err) {
    // A concurrent caller may have created it first; that is fine.
    if (!(await hasOffscreenDocument())) throw err;
  }
}

// A freshly created offscreen document may not have registered its message
// listener yet, so retry until delivery succeeds.
async function sendToOffscreen(payload, attempts = 20, delayMs = 150) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await chrome.runtime.sendMessage(payload);
      return;
    } catch (err) {
      if (attempt === attempts - 1) throw err;
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
}

/**
 * Start one crawl. The offscreen document reports progress and its final
 * result by broadcasting to `target: "panel"`; this worker relays those to any
 * connected web page as well as the side panel.
 */
async function startCrawl(text, settings) {
  await ensureOffscreenDocument();
  await sendToOffscreen({
    target: "offscreen",
    type: "START",
    text,
    settings: settings || {},
  });
}

/** Send a progress/result/error payload to the side panel and any web pages. */
function broadcastPanel(payload) {
  const message = { target: "panel", ...payload };
  relayToExternalPorts(message);
  try {
    const sent = chrome.runtime.sendMessage(message);
    if (sent && typeof sent.catch === "function") sent.catch(() => {});
  } catch {
    /* no receiving end */
  }
}

function relayToExternalPorts(payload) {
  for (const port of externalPorts) {
    try {
      port.postMessage(payload);
    } catch {
      externalPorts.delete(port);
    }
  }
}

/** Start a crawl, reporting start failures to whoever asked for it. */
async function handleRun(text, settings, reportError) {
  try {
    await startCrawl(text, settings);
    return { ok: true };
  } catch (err) {
    const message = `Could not start the crawler: ${err?.message || err}`;
    reportError(message);
    return { ok: false, error: message };
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.warn("setPanelBehavior failed", err));
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  // Progress/result broadcasts from the offscreen crawler: mirror them to any
  // web page that asked for a run, then let the side panel handle them too.
  if (message?.target === "panel") {
    relayToExternalPorts(message);
    return false;
  }

  if (message?.target !== "background") return false;

  if (message.type === "RUN") {
    handleRun(message.text, message.settings, (msg) =>
      broadcastPanel({ type: "ERROR", message: msg }),
    ).then(sendResponse);
    return true; // keep the message channel open for the async response
  }

  return false;
});

const SESSION_KEY = "session";

/**
 * Store a session handed over by our own website.
 *
 * Reporting a scam requires a signed-in user, but the extension has no OAuth
 * flow of its own: the site signs the user in (Google, via Neon Auth) and pushes
 * the short-lived JWT here. The side panel then posts reports with it as a
 * bearer token, and stores the identity so it can show whose account it is.
 */
async function storeExternalSession(message, sender) {
  const origin = sender?.origin;
  if (origin && !ALLOWED_EXTERNAL_ORIGIN.test(origin)) {
    return { ok: false, error: "origin not allowed" };
  }
  const token = typeof message.token === "string" ? message.token : "";
  const user = message.user || {};
  if (!token || !user.id) {
    return { ok: false, error: "incomplete session" };
  }
  await chrome.storage.local.set({
    [SESSION_KEY]: {
      token,
      user: {
        id: String(user.id),
        email: user.email || null,
        name: user.name || null,
      },
      receivedAt: Date.now(),
    },
  });
  return { ok: true };
}

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (message?.type !== "AUTH_SESSION") return false;
  storeExternalSession(message, sender)
    .then(sendResponse)
    .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }));
  return true; // keep the channel open for the async reply
});

// A web page (our own site) drives a run over a Port so it can receive progress
// and the final verdict. Ports keep this service worker alive while connected.
chrome.runtime.onConnectExternal.addListener((port) => {
  if (port.name !== "fakee-bridge") return;

  // Allow when Chrome (via externally_connectable) already vetted the sender and
  // gave us no origin to check; reject an explicitly disallowed origin.
  const origin = port.sender?.origin;
  if (origin && !ALLOWED_EXTERNAL_ORIGIN.test(origin)) {
    console.warn("Fakee: rejected external connection from", origin);
    port.disconnect();
    return;
  }

  externalPorts.add(port);
  port.onDisconnect.addListener(() => externalPorts.delete(port));

  port.onMessage.addListener((message) => {
    if (message?.type === "PING") {
      port.postMessage({ type: "PONG", name: "Fakee", version: chrome.runtime.getManifest().version });
      return;
    }

    if (message?.type === "RUN") {
      handleRun(message.text, message.settings, (msg) =>
        port.postMessage({ type: "ERROR", message: msg }),
      );
    }
  });
});
