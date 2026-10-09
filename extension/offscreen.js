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
  let searched = 0;
  report({ type: "PROGRESS", phase: `Searching ${items.length} queries`, done: 0, total: items.length });
  await mapLimit(items, 4, async (item) => {
    let results = [];
    try {
      results = await searchWeb(item.query, cfg);
    } catch {
      results = []; // one blocked query must not abort the crawl
    }
    for (const r of results) {
      if (!found.has(r.url)) found.set(r.url, { ...r, query: item.query, category: item.category });
    }
    searched += 1;
    report({
      type: "PROGRESS",
      phase: "Searching",
      done: searched,
      total: items.length,
      note: `${found.size} pages found`,
    });
  });

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

async function searchWeb(query, cfg) {
  if (cfg.searchProvider === "brave" && cfg.braveKey) {
    return searchBrave(query, cfg);
  }
  return searchDuckDuckGo(query, cfg);
}

async function searchDuckDuckGo(query, cfg) {
  const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  const response = await fetchWithTimeout(url, { headers: { Accept: "text/html" } }, 15000);
  if (!response.ok) throw new Error(`DuckDuckGo returned ${response.status}`);
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
  return out;
}

async function searchBrave(query, cfg) {
  const url = `https://api.search.brave.com/res/v1/web/search?count=${cfg.perQuery}&q=${encodeURIComponent(query)}`;
  const response = await fetchWithTimeout(
    url,
    { headers: { Accept: "application/json", "X-Subscription-Token": cfg.braveKey } },
    15000,
  );
  if (!response.ok) throw new Error(`Brave Search returned ${response.status}`);
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

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
      credentials: "include",
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
