// Offscreen crawler.
//
// Runs in an offscreen document so it has a DOM (DOMParser) and is not killed
// by the MV3 service-worker idle timeout. It:
//   1. asks the backend to plan queries (/queries),
//   2. searches the web from the user's browser (free — no API credits),
//   3. fetches and extracts readable text from each result page,
//   4. posts the captured pages to /investigate/with-evidence for reasoning.
//
// Nothing is rendered here; the side panel shows progress and the result.

const DEFAULT_SETTINGS = {
  apiBase: "http://localhost:8000/api",
  searchProvider: "duckduckgo", // "duckduckgo" (free, no key) | "brave"
  braveKey: "",
  includeAnalyst: true,
  perQuery: 6,
  maxPages: 40,
  maxPerDomain: 3,
};

let running = false;

chrome.runtime.onMessage.addListener((message) => {
  if (message?.target !== "offscreen" || message.type !== "START") return;
  if (running) {
    report({ type: "ERROR", message: "A crawl is already running." });
    return;
  }
  running = true;
  runCrawl(message.text, { ...DEFAULT_SETTINGS, ...(message.settings || {}) })
    .catch((err) => report({ type: "ERROR", message: err?.message || String(err) }))
    .finally(() => {
      running = false;
    });
});

function report(payload) {
  // The side panel may be closed; ignore "no receiving end" failures.
  try {
    chrome.runtime.sendMessage({ target: "panel", ...payload });
  } catch {
    /* no listener */
  }
}

async function runCrawl(text, cfg) {
  report({ type: "PROGRESS", phase: "Planning queries", done: 0, total: 1 });

  const plan = await postJson(`${cfg.apiBase}/queries`, { text });
  const items = [];
  for (const group of plan.queries || []) {
    for (const query of group.queries || []) {
      items.push({ query, category: group.category });
    }
  }
  if (items.length === 0) {
    report({ type: "ERROR", message: "No search queries could be generated from that input." });
    return;
  }

  // --- 2. Search (discovery) -------------------------------------------
  const found = new Map(); // url -> {url,title,snippet,query,category}
  const primary = primaryProvider(cfg);
  report({ type: "PROGRESS", phase: `Searching ${items.length} queries`, done: 0, total: items.length });
  let searchError = await searchPass(items, primary, cfg, found, (done) =>
    report({
      type: "PROGRESS",
      phase: "Searching",
      done,
      total: items.length,
      note: `${found.size} pages found`,
    }),
  );

  // A provider that returns nothing is usually a block, not an empty web: search
  // engines answer automated fetches with a blank page and no error (DuckDuckGo
  // replays HTTP 202 with zero result nodes). Try the other provider once before
  // giving up, so a provider-specific block becomes a working run rather than a
  // dead end.
  if (found.size === 0) {
    const fallback = otherProvider(cfg);
    if (fallback) {
      const label = providerLabel(fallback);
      report({
        type: "PROGRESS",
        phase: `Searching (${label})`,
        done: 0,
        total: items.length,
        note: `retrying with ${label}`,
      });
      const fallbackError = await searchPass(
        items,
        fallback,
        { ...cfg, searchProvider: fallback },
        found,
        (done) =>
          report({
            type: "PROGRESS",
            phase: `Searching (${label})`,
            done,
            total: items.length,
            note: `${found.size} pages found`,
          }),
      );
      if (fallbackError) {
        searchError = searchError ? `${searchError}; ${fallbackError}` : fallbackError;
      }
    }
  }

  // --- 3. Scrape (read each page) --------------------------------------
  const targets = pickTargets([...found.values()], cfg);
  const pages = [];
  let read = 0;
  report({ type: "PROGRESS", phase: `Reading ${targets.length} pages`, done: 0, total: targets.length });
  await mapLimit(targets, 6, async (t) => {
    let extracted = null;
    try {
      extracted = await scrapePage(t.url);
    } catch {
      extracted = null;
    }
    if (extracted && extracted.text.length >= 150) {
      pages.push({
        url: t.url,
        title: extracted.title || t.title || "",
        text: extracted.text,
        query: t.query,
        category: t.category,
      });
    }
    read += 1;
    report({
      type: "PROGRESS",
      phase: "Reading pages",
      done: read,
      total: targets.length,
      note: `${pages.length} readable`,
    });
  });

  // A crawl that read nothing must not be sent on as if it were evidence: the
  // backend refuses to fabricate mock results for a client capture, so stopping
  // here turns a silent "verdict built on nothing" into something the user can
  // act on. Name the half that failed — discovery (search) or reading (scrape).
  if (pages.length === 0) {
    let reason;
    if (found.size === 0) {
      if (cfg.searchProvider === "brave" && !cfg.braveKey) {
        // Selected Brave but keyed nothing: the crawler fell back to DuckDuckGo.
        // Say that plainly rather than blaming a provider that was never used.
        reason = "Brave is selected but no Brave API key is set";
      } else {
        reason = `the ${providerLabel(primary)} search returned no usable results`;
        // The provider's own error (bad key, rate limit, block) is the useful
        // part; without it, "no results" hides the real cause.
        if (searchError) reason += ` — ${searchError}`;
      }
    } else {
      reason = `none of the ${targets.length} result pages could be read`;
    }
    report({
      type: "ERROR",
      message:
        `Nothing could be captured for analysis: ${reason}. ` +
        "Check the search provider and API key in Settings, then try again.",
    });
    return;
  }

  // --- 4. Reason server-side -------------------------------------------
  report({ type: "PROGRESS", phase: "Analyzing evidence", done: 0, total: 1 });
  const result = await postJson(`${cfg.apiBase}/investigate/with-evidence`, {
    text,
    pages,
    // Do NOT store a company record for a plain search. Nothing is persisted
    // until the user explicitly reports the posting (the "Report as scam"
    // button -> POST /reports).
    persist: false,
    structured_input: plan.input,
    // Cost knob: the analyst read is bundled into the same Groq call, so it is
    // not an extra request — but users on a tight budget can turn it off.
    include_analyst: cfg.includeAnalyst !== false,
  });
  report({ type: "RESULT", result, pagesCaptured: pages.length });
}

function pickTargets(candidates, cfg) {
  const perDomain = new Map();
  const out = [];
  for (const c of candidates) {
    const domain = safeDomain(c.url);
    const count = perDomain.get(domain) || 0;
    if (count >= cfg.maxPerDomain) continue;
    perDomain.set(domain, count + 1);
    out.push(c);
    if (out.length >= cfg.maxPages) break;
  }
  return out;
}

// ---------------------------------------------------------------- search ----

/** The provider to try first: the configured one (Brave only when keyed). */
function primaryProvider(cfg) {
  return cfg.searchProvider === "brave" && cfg.braveKey ? "brave" : "duckduckgo";
}

/** The provider to fall back to when the first finds nothing, if one is set up. */
function otherProvider(cfg) {
  if (primaryProvider(cfg) === "duckduckgo") return cfg.braveKey ? "brave" : null;
  return "duckduckgo";
}

function providerLabel(provider) {
  return provider === "brave" ? "Brave" : "DuckDuckGo";
}

async function searchWith(provider, query, cfg) {
  return provider === "brave" ? searchBrave(query, cfg) : searchDuckDuckGo(query, cfg);
}

/**
 * Run every query through one provider and collect what it returns.
 *
 * A single blocked query must not abort the crawl, but its failure is kept so a
 * run that found nothing can report *why* instead of guessing.
 */
async function searchPass(items, provider, cfg, found, onProgress) {
  let firstError = "";
  let done = 0;
  await mapLimit(items, 4, async (item) => {
    let results = [];
    try {
      results = await searchWith(provider, item.query, cfg);
    } catch (err) {
      if (!firstError) firstError = err?.message || String(err);
    }
    for (const r of results) {
      if (!found.has(r.url)) found.set(r.url, { ...r, query: item.query, category: item.category });
    }
    done += 1;
    onProgress(done);
  });
  return firstError;
}

async function searchDuckDuckGo(query, cfg) {
  const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  const response = await fetchWithTimeout(url, { headers: { Accept: "text/html" } }, 15000);
  if (!response.ok) throw new Error(`DuckDuckGo HTTP ${response.status}`);
  const html = await response.text();
  const doc = new DOMParser().parseFromString(html, "text/html");

  const out = [];
  for (const el of doc.querySelectorAll(".result")) {
    const anchor = el.querySelector("a.result__a");
    if (!anchor) continue;
    const href = decodeDuckDuckGoUrl(anchor.getAttribute("href") || "");
    if (!/^https?:/i.test(href)) continue;
    const snippetEl = el.querySelector(".result__snippet");
    out.push({
      url: href,
      title: (anchor.textContent || "").trim(),
      snippet: (snippetEl?.textContent || "").trim(),
    });
    if (out.length >= cfg.perQuery) break;
  }
  // An empty list here is not "no such page exists": DuckDuckGo answers
  // automated fetches with HTTP 202 and a page carrying no result nodes. Report
  // it as a block so it is never mistaken for a genuinely empty web.
  if (out.length === 0) {
    throw new Error(`DuckDuckGo returned no results (HTTP ${response.status}, likely blocked)`);
  }
  return out;
}

async function searchBrave(query, cfg) {
  const url = `https://api.search.brave.com/res/v1/web/search?count=${cfg.perQuery}&q=${encodeURIComponent(query)}`;
  const headers = { Accept: "application/json", "X-Subscription-Token": cfg.braveKey };

  // The free tier allows one request a second while the crawler fires several
  // queries at once, so a 429 is expected rather than exceptional. Honour
  // Retry-After when Brave sends it, and retry a couple of times.
  let response;
  for (let attempt = 0; ; attempt += 1) {
    response = await fetchWithTimeout(url, { headers }, 15000);
    if (response.status !== 429 || attempt >= 2) break;
    const retryAfter = Number(response.headers.get("retry-after"));
    await sleep(Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1000 : 1100);
  }
  if (!response.ok) {
    // 401/403: the key is wrong, expired, or not on a Web Search plan.
    // 429: the rate or monthly limit is spent. Surface it — a swallowed error
    // here reads as "no results" and hides the cause.
    throw new Error(`Brave Search HTTP ${response.status}${await errorHint(response)}`);
  }

  const data = await response.json();
  const out = [];
  for (const r of data.web?.results || []) {
    if (!r.url) continue;
    out.push({ url: r.url, title: r.title || "", snippet: r.description || "" });
    if (out.length >= cfg.perQuery) break;
  }
  return out;
}

// DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<encoded target>.
function decodeDuckDuckGoUrl(href) {
  try {
    const parsed = new URL(href, "https://duckduckgo.com");
    const target = parsed.searchParams.get("uddg");
    if (target) return decodeURIComponent(target);
    if (href.startsWith("//")) return `https:${href}`;
    return href;
  } catch {
    return href;
  }
}

// ---------------------------------------------------------------- scrape ----

async function scrapePage(url) {
  const response = await fetchWithTimeout(
    url,
    { headers: { Accept: "text/html,application/xhtml+xml" } },
    20000,
  );
  if (!response.ok) return null;

  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType && !contentType.includes("html") && !contentType.includes("xml")) {
    return null; // skip PDFs, images, JSON, etc.
  }

  const html = await response.text();
  if (!html || html.length < 100) return null;

  const doc = new DOMParser().parseFromString(html, "text/html");
  return extractReadable(doc);
}

// A lightweight, dependency-free readability pass: drop chrome/boilerplate and
// keep the densest text block. Good enough for the pattern engine downstream.
function extractReadable(doc) {
  const title = pickTitle(doc);

  const strip = doc.querySelectorAll(
    "script,style,noscript,svg,iframe,canvas,template,form,button,input,select,textarea," +
      "nav,header,footer,aside,[role=navigation],[role=banner],[role=contentinfo]," +
      "[aria-hidden=true],[hidden]",
  );
  strip.forEach((el) => el.remove());

  const candidates = [
    doc.querySelector("article"),
    doc.querySelector("main"),
    doc.querySelector("[role=main]"),
    doc.body,
  ].filter(Boolean);

  let best = "";
  for (const node of candidates) {
    const text = normalize(node.textContent || "");
    if (text.length > best.length) best = text;
  }

  if (best.length < 200 && doc.body) {
    best = normalize(doc.body.textContent || "");
  }
  return { title, text: best.slice(0, 8000) };
}

function pickTitle(doc) {
  const og = doc.querySelector('meta[property="og:title"]')?.getAttribute("content");
  if (og && og.trim()) return og.trim().slice(0, 300);
  const h1 = doc.querySelector("h1")?.textContent;
  if (h1 && h1.trim()) return h1.trim().slice(0, 300);
  const t = doc.querySelector("title")?.textContent;
  return (t || "").trim().slice(0, 300);
}

function normalize(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

// --------------------------------------------------------------- helpers ----

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A short `<message>` from a provider's JSON error body, when it sends one. */
async function errorHint(response) {
  try {
    const match = (await response.text()).match(/"message"\s*:\s*"([^"]{0,140})"/);
    return match ? `: ${match[1]}` : "";
  } catch {
    return "";
  }
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
      // Never send the user's cookies to a search endpoint or a scraped site:
      // neither needs the user's session, and sending them would leak the
      // user's identity to every page the crawler touches.
      credentials: "omit",
      redirect: "follow",
      cache: "no-store",
    });
  } finally {
    clearTimeout(timer);
  }
}

async function postJson(url, body) {
  const response = await fetchWithTimeout(
    url,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    180000,
  );
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const parsed = await response.json();
      if (parsed?.detail) detail = typeof parsed.detail === "string" ? parsed.detail : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return response.json();
}

async function mapLimit(items, limit, fn) {
  let cursor = 0;
  const workerCount = Math.max(1, Math.min(limit, items.length));
  const workers = Array.from({ length: workerCount }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      try {
        await fn(items[index], index);
      } catch {
        /* per-item failures are handled by the caller */
      }
    }
  });
  await Promise.all(workers);
}

function safeDomain(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
