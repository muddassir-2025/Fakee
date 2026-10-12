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
  // "duckduckgo" (free, no key) | "brave" | "tinyfish" | "serpdive" (free
  // tiers, each with its own API key) | "searxng" (self-hosted, free)
  searchProvider: "duckduckgo",
  braveKey: "",
  searxngUrl: "",
  tinyfishKey: "",
  serpdiveKey: "",
  includeAnalyst: true,
  perQuery: 6,
  maxPages: 40,
  maxPerDomain: 3,
};

let running = false;
// The last Brave allowance seen in a response header, reported to the backend
// once per crawl (see `noteBraveQuota`). Null when Brave was not used.
let braveQuota = null;

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
  braveQuota = null;
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
  // Try the configured provider first. SearXNG chains onto the others (see
  // `providerChain`); DuckDuckGo and Brave are used exactly as chosen.
  const found = new Map(); // url -> {url,title,snippet,query,category,provider}
  const chain = providerChain(cfg);
  const primary = chain[0];
  const warnings = [];
  let searchError = "";

  for (let index = 0; index < chain.length; index += 1) {
    const provider = chain[index];
    report({
      type: "PROGRESS",
      phase: `Searching (${providerLabel(provider)})`,
      done: 0,
      total: items.length,
    });
    const error = await searchPass(items, provider, cfg, found, (done) =>
      report({
        type: "PROGRESS",
        phase: `Searching (${providerLabel(provider)})`,
        done,
        total: items.length,
        note: `${found.size} pages found`,
      }),
    );
    if (error && !searchError) searchError = error;
    if (found.size > 0) break;
    if (index < chain.length - 1) {
      // Non-blocking: the run continues on the next provider. The panel shows
      // this so a dead or blocked SearXNG instance is not a silent dead end.
      warnings.push(
        `${providerLabel(provider)} returned no usable results${error ? ` (${error})` : ""}; ` +
          `used ${providerLabel(chain[index + 1])} instead.`,
      );
    }
  }

  // Brave is the one allowance a run spends that the backend cannot see, since
  // the search happens here. Start the report but do not wait on it before
  // scraping — it is display-only, and all that matters is that it has landed by
  // the time the panel refreshes the bar after RESULT.
  const budgetReport = reportBraveQuota(cfg);

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
        provider: t.provider,
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
      reason = `the ${providerLabel(primary)} search returned no usable results`;
      // The provider's own error (bad key, rate limit, block) is the useful
      // part; without it, "no results" hides the real cause.
      if (searchError) reason += ` — ${searchError}`;
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
  // Which provider supplied each page, so the evidence list can label a source,
  // plus any non-blocking provider warnings from the chain above.
  const providers = {};
  for (const page of pages) {
    if (page.provider) providers[page.url] = providerLabel(page.provider);
  }
  await budgetReport;
  report({ type: "RESULT", result, pagesCaptured: pages.length, providers, warnings });
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

/**
 * Providers queried one at a time, with a gap between queries.
 *
 * The hosted pair are capped per key (TinyFish allows 30 requests a minute;
 * SERPdive's free `krill` model answers one request at a time by design), and a
 * SearXNG instance is a single machine the user runs. DuckDuckGo and Brave keep
 * the concurrent burst they were measured with.
 */
const GAP_MS = { searxng: 700, tinyfish: 700, serpdive: 700 };

const PROVIDERS = ["brave", "tinyfish", "serpdive", "searxng"];

const PROVIDER_LABELS = {
  duckduckgo: "DuckDuckGo",
  brave: "Brave",
  tinyfish: "TinyFish",
  serpdive: "SERPdive",
  searxng: "SearXNG",
};

/** The provider exactly as configured, validated against what is supported. */
function configuredProvider(cfg) {
  return PROVIDERS.includes(cfg.searchProvider) ? cfg.searchProvider : "duckduckgo";
}

function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || PROVIDER_LABELS.duckduckgo;
}

/**
 * The providers to try, in order.
 *
 * Only SearXNG chains onto the others. A self-hosted instance is a machine the
 * user runs, so it can be down, unconfigured, or blocked by its own upstream
 * engines; falling back keeps a run working and reports what happened. The
 * DuckDuckGo/Brave choices stay exact — silently answering one with the other
 * was confusing.
 */
function providerChain(cfg) {
  const primary = configuredProvider(cfg);
  if (primary !== "searxng") return [primary];
  const chain = ["searxng"];
  if (cfg.braveKey) chain.push("brave");
  chain.push("duckduckgo");
  return chain;
}

async function searchWith(provider, query, cfg) {
  if (provider === "searxng") return searchSearxng(query, cfg);
  if (provider === "tinyfish") return searchTinyFish(query, cfg);
  if (provider === "serpdive") return searchSerpdive(query, cfg);
  if (provider === "brave") {
    if (!cfg.braveKey) throw new Error("Brave is selected but no Brave API key is set");
    return searchBrave(query, cfg);
  }
  return searchDuckDuckGo(query, cfg);
}

/**
 * Run one query, relaxing a quoted search that comes back empty.
 *
 * The query plan quotes the company name for precision (`"Acme" scam`), but a
 * phrase search for a name that appears nowhere verbatim returns nothing — and
 * a made-up/scam company name is *by definition* one that appears nowhere. Brave
 * enforces the quotes strictly, so an all-quoted plan can return zero results
 * for every query. Dropping the quotes trades a little precision for results
 * that actually exist.
 */
async function searchQuery(provider, query, cfg) {
  const results = await searchWith(provider, query, cfg);
  if (results.length || !query.includes('"')) return results;
  const relaxed = query.replace(/"/g, " ").replace(/\s+/g, " ").trim();
  if (!relaxed || relaxed === query) return results;
  return searchWith(provider, relaxed, cfg);
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
  const runOne = async (item) => {
    let results = [];
    try {
      results = await searchQuery(provider, item.query, cfg);
    } catch (err) {
      if (!firstError) firstError = err?.message || String(err);
    }
    for (const r of results) {
      if (!found.has(r.url)) {
        found.set(r.url, { ...r, query: item.query, category: item.category, provider });
      }
    }
    done += 1;
    onProgress(done);
  };

  // A per-key cap (or a single machine behind the provider) is honoured by
  // querying politely: one at a time with a gap, rather than a burst.
  const gap = GAP_MS[provider];
  if (gap != null) {
    for (let index = 0; index < items.length; index += 1) {
      await runOne(items[index]);
      if (index < items.length - 1) await sleep(gap);
    }
  } else {
    await mapLimit(items, 4, runOne);
  }
  return firstError;
}

// ---------------------------------------------------------------- searxng ---

/**
 * Search a self-hosted SearXNG instance.
 *
 * Requires the instance to expose its JSON output format (`format=json`) — that
 * is off by default in SearXNG, which is why the settings field says so and the
 * panel offers a Test connection button.
 */
async function searchSearxng(query, cfg) {
  const base = (cfg.searxngUrl || "").trim().replace(/\/+$/, "");
  if (!base) throw new Error("No SearXNG instance URL is set");
  const url = `${base}/search?q=${encodeURIComponent(query)}&format=json`;
  const options = { headers: { Accept: "application/json" } };
  const unreachable = new Error(
    "Could not reach your SearXNG instance. Check that the URL is correct and the server is running.",
  );

  let response;
  try {
    response = await fetchWithTimeout(url, options, 20000);
  } catch {
    throw unreachable;
  }
  // The instance (or an engine upstream of it) may be rate limiting: back off
  // once before treating it as a failure.
  if (response.status === 429) {
    await sleep(2000);
    try {
      response = await fetchWithTimeout(url, options, 20000);
    } catch {
      throw unreachable;
    }
  }
  return readSearxngResults(response, cfg);
}

/**
 * Normalize a SearXNG response into the shared result shape.
 *
 * An instance answers in JSON only when its `json` output format is enabled, and
 * even then it may relay a block page its own upstream engines served it. Both
 * look like "no results" if you only read `results`, so they are told apart: a
 * block page is surfaced as such, while anything else unparseable is an empty
 * search — not a fatal error — so the run can continue on another provider.
 */
async function readSearxngResults(response, cfg) {
  const body = await response.text();

  let data = null;
  try {
    data = JSON.parse(body);
  } catch {
    data = null;
  }

  if (data === null) {
    if (/anomaly-modal|bots use DuckDuckGo|captcha|too many requests/i.test(body)) {
      throw new Error(
        "Your SearXNG instance returned a block page. Try again later or check " +
          "your instance's upstream engine status.",
      );
    }
    return []; // Not JSON and not a block page: treat as an empty search.
  }

  const out = [];
  for (const r of data.results || []) {
    if (!r.url) continue;
    out.push({ url: r.url, title: r.title || "", snippet: r.content || "" });
    if (out.length >= cfg.perQuery) break;
  }
  return out;
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
  // DuckDuckGo answers automated fetches with HTTP 202 and a page carrying no
  // result nodes. Report that as a block — but a normal HTTP 200 with no results
  // is a genuine "no matches" page, which must stay an empty list so the caller
  // can relax the query instead of treating it as a failure.
  if (out.length === 0 && (response.status === 202 || /bots use DuckDuckGo|anomaly-modal/i.test(html))) {
    throw new Error("DuckDuckGo blocked the request (HTTP 202)");
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
    // Read the quota headers before deciding what to do with the body: a 429 is
    // exactly when the remaining count matters most.
    noteBraveQuota(response);
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

// ----------------------------------------------------------------- budget ----

/**
 * Remember Brave's remaining allowance from a response's rate-limit headers.
 *
 * Brave reports several windows at once, comma-separated, in the same order
 * across all of these headers:
 *
 *   X-RateLimit-Limit:     1, 15000
 *   X-RateLimit-Policy:    1;w=1, 15000;w=2592000
 *   X-RateLimit-Remaining: 0, 14523
 *   X-RateLimit-Reset:     1, 1419704          (seconds until each resets)
 *
 * The bar tracks the **monthly** window, so the pair with the largest `w=` is
 * kept: the per-second burst limit refills constantly and says nothing about how
 * much of the free allowance is left. If only a burst window is exposed there is
 * no quota to report, and nothing is recorded — a made-up monthly figure would
 * be worse than no figure.
 */
function noteBraveQuota(response) {
  const numbers = (name) => {
    const raw = response.headers.get(name);
    if (!raw) return [];
    return raw
      .split(",")
      .map((part) => Number(part.trim()))
      .filter((value) => Number.isFinite(value));
  };

  const limits = numbers("x-ratelimit-limit");
  const remaining = numbers("x-ratelimit-remaining");
  if (!limits.length || !remaining.length) return;

  const windows = (response.headers.get("x-ratelimit-policy") || "")
    .split(",")
    .map((part) => Number((part.match(/w=(\d+)/) || [])[1]));
  const usable = windows.filter((value) => Number.isFinite(value));

  // No policy header: Brave lists the burst window first and the quota last.
  let index = usable.length ? windows.indexOf(Math.max(...usable)) : limits.length - 1;
  if (!usable.length && index < 0) return;
  if (usable.length && windows[index] < 3600) return;
  if (index >= limits.length || index >= remaining.length) return;

  const resets = numbers("x-ratelimit-reset");
  braveQuota = {
    limit: limits[index],
    remaining: remaining[index],
    reset_seconds: Number.isFinite(resets[index]) ? resets[index] : null,
  };
}

/**
 * Hand the Brave reading to the backend, which owns the shared budget bar.
 *
 * Best-effort by design: the bar is a convenience, so a failed report is
 * swallowed rather than failing an investigation that has already found its
 * evidence.
 */
async function reportBraveQuota(cfg) {
  if (!braveQuota) return;
  try {
    await postJson(`${cfg.apiBase}/budget/brave`, braveQuota);
  } catch {
    /* display-only */
  }
}

// ---------------------------------------------------------------- tinyfish --

/**
 * Search with TinyFish (`api.search.tinyfish.ai`).
 *
 * A keyed free tier: 30 requests a minute, 12,000 a day per key, resetting at
 * 00:00 UTC. The endpoint takes no result-count parameter, so `perQuery` is
 * applied to whatever comes back.
 */
async function searchTinyFish(query, cfg) {
  if (!cfg.tinyfishKey) {
    throw new Error("TinyFish is selected but no TinyFish API key is set");
  }
  const url = `https://api.search.tinyfish.ai?query=${encodeURIComponent(query)}`;
  const options = { headers: { Accept: "application/json", "X-API-Key": cfg.tinyfishKey } };

  let response;
  try {
    response = await fetchRetrying(url, options, 20000);
  } catch {
    throw new Error("Could not reach TinyFish. Check your connection and try again.");
  }
  if (!response.ok) throw new Error(`TinyFish ${await tinyFishError(response)}`);

  const data = await response.json();
  const out = [];
  for (const r of data.results || []) {
    if (!r.url) continue;
    out.push({ url: r.url, title: r.title || "", snippet: r.snippet || "" });
    if (out.length >= cfg.perQuery) break;
  }
  return out;
}

/** Say what went wrong in the terms that tell the user how to fix it. */
async function tinyFishError(response) {
  const hint = await errorHint(response);
  if (response.status === 401 || response.status === 403) {
    return `rejected the API key (HTTP ${response.status})${hint}`;
  }
  if (response.status === 402) {
    return "free daily allowance is used up (HTTP 402) — it resets at 00:00 UTC";
  }
  if (response.status === 429) {
    return "rate limit reached (HTTP 429) — the free tier allows 30 requests a minute";
  }
  return `HTTP ${response.status}${hint}`;
}

// ---------------------------------------------------------------- serpdive --

/**
 * Search with SERPdive (`api.serpdive.com/v1/search`).
 *
 * Runs on its free `krill` model: no credits are spent, it is unlimited under
 * fair use, and it answers one request at a time — hence the serial pacing in
 * `GAP_MS`. `krill` returns the page content already extracted, which maps onto
 * the shared `snippet` field; it does not support the `answer` option, so none
 * is requested.
 */
async function searchSerpdive(query, cfg) {
  if (!cfg.serpdiveKey) {
    throw new Error("SERPdive is selected but no SERPdive API key is set");
  }
  const options = {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${cfg.serpdiveKey}`,
    },
    body: JSON.stringify({
      query,
      model: "krill",
      // The API accepts 1-10; the crawler's own cap is what really applies.
      max_results: Math.max(1, Math.min(10, cfg.perQuery || 6)),
    }),
  };

  let response;
  try {
    response = await fetchRetrying("https://api.serpdive.com/v1/search", options, 30000);
  } catch {
    throw new Error("Could not reach SERPdive. Check your connection and try again.");
  }
  if (!response.ok) throw new Error(`SERPdive ${await serpdiveError(response)}`);

  const data = await response.json();
  const out = [];
  for (const r of data.results || []) {
    if (!r.url) continue;
    out.push({ url: r.url, title: r.title || "", snippet: r.content || "" });
    if (out.length >= cfg.perQuery) break;
  }
  return out;
}

/** Say what went wrong in the terms that tell the user how to fix it. */
async function serpdiveError(response) {
  const hint = await errorHint(response);
  if (response.status === 401) return `rejected the API key (HTTP 401)${hint}`;
  if (response.status === 429) return `rate limit reached (HTTP 429)${hint}`;
  if (response.status >= 500) {
    return `is unavailable right now (HTTP ${response.status})${hint}`;
  }
  return `HTTP ${response.status}${hint}`;
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

// Statuses that mean "the request was fine, ask again later": rate limiting and
// transient upstream failures. Brave does its own retrying (see `searchBrave`).
const RETRYABLE_STATUSES = new Set([429, 502, 503]);

/**
 * Fetch, retrying those transient responses a couple of times.
 *
 * A 429 is expected rather than exceptional on a free tier, so give up only
 * after the retries; `Retry-After` is honoured when the provider sends one.
 */
async function fetchRetrying(url, options, timeoutMs, retries = 2) {
  let response = await fetchWithTimeout(url, options, timeoutMs);
  for (let attempt = 0; attempt < retries; attempt += 1) {
    if (!RETRYABLE_STATUSES.has(response.status)) break;
    const retryAfter = Number(response.headers.get("retry-after"));
    const waitMs =
      Number.isFinite(retryAfter) && retryAfter > 0
        ? Math.min(retryAfter, 10) * 1000
        : 1100 * (attempt + 1);
    await sleep(waitMs);
    response = await fetchWithTimeout(url, options, timeoutMs);
  }
  return response;
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
