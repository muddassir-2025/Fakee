// Side panel controller. Talks to the background worker to start a crawl and
// renders progress + the final risk result sent back by the offscreen crawler.

const DEFAULT_SETTINGS = {
  apiBase: "http://localhost:8000/api",
  // Where the site lives. Reporting signs the user in there, then the page hands
  // the session back to this extension (see `?ext=` in the report flow).
  siteUrl: "http://localhost:5173",
  searchProvider: "duckduckgo",
  braveKey: "",
  includeAnalyst: true,
};

const THEME_KEY = "fjd-theme";
const SESSION_KEY = "session";

const els = {
  input: document.getElementById("input"),
  apiBase: document.getElementById("api-base"),
  siteUrl: document.getElementById("site-url"),
  provider: document.getElementById("provider"),
  braveKey: document.getElementById("brave-key"),
  includeAnalyst: document.getElementById("include-analyst"),
  run: document.getElementById("run"),
  progress: document.getElementById("progress"),
  progressPhase: document.querySelector(".progress-phase"),
  progressNote: document.querySelector(".progress-note"),
  error: document.getElementById("error"),
  result: document.getElementById("result"),
  empty: document.getElementById("empty"),
  quota: document.getElementById("quota"),
  quotaValue: document.getElementById("quota-value"),
  quotaFill: document.getElementById("quota-fill"),
  quotaNote: document.getElementById("quota-note"),
  account: document.getElementById("account"),
  accountEmail: document.getElementById("account-email"),
  signOut: document.getElementById("sign-out"),
  reportsToggle: document.getElementById("my-reports-toggle"),
  reports: document.getElementById("my-reports"),
  theme: document.getElementById("theme-toggle"),
};

let settings = { ...DEFAULT_SETTINGS };
// Captured at run time so the report button can send exactly what was analyzed.
let lastRun = { text: "", apiBase: DEFAULT_SETTINGS.apiBase, riskLevel: null, riskScore: null };
// The signed-in user, as handed over by the website. `stale` is set when the
// API rejects the token (it expires after 15 minutes), which is the signal to
// send the user through sign-in again for a fresh one.
let session = null;
let sessionStale = false;
// Kept so the panel can be re-rendered (after signing in or out) without
// re-running the investigation.
let lastResult = null;
let lastPagesCaptured = 0;

// ------------------------------------------------------------------ theme ---
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  els.theme.textContent = theme === "dark" ? "☀" : "☾";
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* ignore */
  }
}

function initTheme() {
  let theme = null;
  try {
    theme = localStorage.getItem(THEME_KEY);
  } catch {
    /* ignore */
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyTheme(theme);
}
els.theme.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

// --------------------------------------------------------------- settings ---
async function loadSettings() {
  const stored = await chrome.storage.local.get([
    "apiBase",
    "siteUrl",
    "searchProvider",
    "braveKey",
    "includeAnalyst",
    SESSION_KEY,
  ]);
  settings = { ...DEFAULT_SETTINGS, ...stored };
  session = readStoredSession(stored[SESSION_KEY]);
  els.apiBase.value = settings.apiBase;
  els.siteUrl.value = settings.siteUrl;
  els.provider.value = settings.searchProvider;
  els.braveKey.value = settings.braveKey;
  els.includeAnalyst.checked = settings.includeAnalyst !== false;
}

function readSettingsFromForm() {
  settings = {
    apiBase: els.apiBase.value.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.apiBase,
    siteUrl: els.siteUrl.value.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.siteUrl,
    searchProvider: els.provider.value,
    braveKey: els.braveKey.value.trim(),
    includeAnalyst: els.includeAnalyst.checked,
    perQuery: 6,
    maxPages: 40,
  };
  chrome.storage.local.set(settings);
  return settings;
}

// ------------------------------------------------------------------- run ----
els.run.addEventListener("click", async () => {
  const text = els.input.value.trim();
  if (text.length < 3) {
    showError("Paste some details about the job or company first.");
    return;
  }

  const config = readSettingsFromForm();
  hide(els.error);
  hide(els.result);
  hide(els.empty);
  show(els.progress);
  setProgress("Starting…", "");
  els.run.disabled = true;

  try {
    const response = await chrome.runtime.sendMessage({
      target: "background",
      type: "RUN",
      text,
      settings: config,
    });
    if (!response?.ok) {
      throw new Error(response?.error || "Could not start the crawler.");
    }
    lastRun = { text, apiBase: config.apiBase };
    chrome.storage.local.set({ lastInput: text });
  } catch (err) {
    finishRun();
    showError(err?.message || String(err));
  }
});

// Ctrl/Cmd + Enter runs the investigation without reaching for the mouse.
els.input.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    if (!els.run.disabled) els.run.click();
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.target !== "panel") return;

  if (message.type === "PROGRESS") {
    const counter = message.total ? `${message.done}/${message.total}` : "";
    setProgress(`${message.phase}${counter ? ` · ${counter}` : ""}`, message.note || "");
  } else if (message.type === "RESULT") {
    finishRun();
    lastResult = message.result;
    lastPagesCaptured = message.pagesCaptured || 0;
    renderResult(message.result, message.pagesCaptured);
    refreshQuota();
  } else if (message.type === "ERROR") {
    finishRun();
    showError(message.message);
  }
});

function finishRun() {
  els.run.disabled = false;
  hide(els.progress);
}

// ------------------------------------------------------------------ quota ---
// Each investigation costs two Groq requests (query plan + evidence).
const REQUESTS_PER_INVESTIGATION = 2;

async function refreshQuota() {
  try {
    const response = await fetch(`${settings.apiBase}/groq/quota`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    renderQuota(await response.json());
  } catch {
    renderQuota(null);
  }
}

function renderQuota(quota) {
  if (!quota || !quota.enabled) {
    hide(els.quota);
    return;
  }
  show(els.quota);

  const limit = quota.limit_requests;
  const remaining = quota.remaining_requests;
  if (!limit || remaining == null) {
    els.quotaValue.textContent = "—";
    els.quotaFill.style.width = "0%";
    els.quotaFill.className = "quota-fill";
    els.quotaNote.textContent = "Run an investigation to load the Groq budget.";
    return;
  }

  const pct = Math.max(0, Math.min(100, (remaining / limit) * 100));
  els.quotaValue.textContent = `${remaining} / ${limit} requests left`;
  els.quotaFill.style.width = `${pct.toFixed(1)}%`;
  els.quotaFill.className = `quota-fill ${pct > 50 ? "" : pct > 20 ? "warn" : "danger"}`.trim();

  const runs = Math.floor(remaining / REQUESTS_PER_INVESTIGATION);
  let note = `≈ ${runs} more investigation${runs === 1 ? "" : "s"} today`;
  if (quota.remaining_tokens != null && quota.limit_tokens != null) {
    note += ` · ${quota.remaining_tokens} / ${quota.limit_tokens} tokens this minute`;
  }
  els.quotaNote.textContent = note;
}

// ---------------------------------------------------------------- render ----
function renderResult(result, pagesCaptured) {
  hide(els.error);
  hide(els.empty);
  els.result.replaceChildren();

  const { risk } = result;
  // Remember the verdict so a report filed from here carries the score the
  // reporter was looking at (the admin dashboard shows it).
  lastRun.riskLevel = risk.level || null;
  lastRun.riskScore = typeof risk.score === "number" ? risk.score : null;
  els.result.append(renderVerdict(risk));

  if (result.cached) {
    els.result.append(
      el("p", "cached-note muted", "Served from cache — a recent check of this company.")
    );
  }

  if (risk.analyst) {
    els.result.append(renderAnalyst(risk.analyst));
  }
  if (risk.checklist?.length) {
    els.result.append(renderChecklist(risk.checklist));
  }
  if (risk.signals?.length) {
    els.result.append(renderSignals(risk.signals));
  }
  if (risk.trust_signals?.length) {
    els.result.append(renderTrust(risk.trust_score, risk.trust_signals));
  }
  if (result.investigation?.coverage) {
    els.result.append(renderCoverage(result.investigation.coverage));
  }
  if (result.investigation?.notable_findings?.length) {
    els.result.append(renderFindings(result.investigation.notable_findings));
  }
  if (result.investigation?.evidence?.length) {
    els.result.append(renderEvidence(result.investigation.evidence, pagesCaptured));
  }

  els.result.append(renderReportAction());

  show(els.result);
}

function renderVerdict(risk) {
  const wrap = el("div", `verdict level-${risk.level}`);

  const scoreRow = el("div", "score-row");
  const score = el("span", "score", String(risk.score));
  score.style.color = `var(--${colorVar(risk.level)})`;
  scoreRow.append(score, el("span", "score-max", "/100"));
  scoreRow.append(el("span", `level-tag level-${risk.level}`, `${risk.level} RISK`));
  wrap.append(scoreRow);

  if (risk.status) {
    const statusRow = el("div", "status-row");
    statusRow.append(
      el("span", `status-chip status-${risk.status}`, STATUS_TEXT[risk.status] || risk.status),
    );
    if (risk.deadline) {
      statusRow.append(
        el("span", "deadline muted", `Deadline: ${risk.deadline}${risk.expired ? " (passed)" : ""}`),
      );
    }
    wrap.append(statusRow);
  }

  wrap.append(el("h2", "headline", risk.headline));
  wrap.append(el("p", "summary", risk.summary));

  const rec = el("div", "recommendation");
  rec.append(el("strong", "", "What to do"));
  rec.append(el("p", "", risk.recommendation));
  wrap.append(rec);

  return wrap;
}

const STATUS_TEXT = {
  FRAUDULENT_VERIFIED: "Fraudulent (verified)",
  LEGITIMATE_VERIFIED: "Legitimate (verified)",
  LIKELY_LEGITIMATE_UNVERIFIED: "Likely legitimate (unverified)",
  NEEDS_VERIFICATION: "Needs verification",
  EXPIRED: "Expired — not necessarily fake",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  CONFLICTING_EVIDENCE: "Conflicting evidence",
};

function renderCoverage(coverage) {
  const section = el("section", "card coverage");
  const head = el("div", "coverage-head");
  head.append(el("h2", "section-title", "Evidence coverage"));
  head.append(
    el(
      "span",
      "coverage-status muted",
      coverage.sufficient ? "sufficient to judge" : "insufficient to judge",
    ),
  );
  section.append(head);

  const row = el("div", "coverage-row");
  const stats = [
    ["Pages captured", coverage.pages_captured],
    ["Sent to model", coverage.pages_in_prompt],
    ["Rules-only", coverage.pages_rules_only],
    ["Signal sentences", coverage.signal_sentences],
  ];
  for (const [label, value] of stats) {
    const item = el("div", "coverage-stat");
    item.append(el("span", "coverage-value", String(value ?? 0)));
    item.append(el("span", "coverage-label", label));
    row.append(item);
  }
  section.append(row);
  if (coverage.note) section.append(el("p", "coverage-note muted", coverage.note));
  return section;
}

function renderFlags(title, flags, kind) {
  const wrap = el("div", "flag-group");
  wrap.append(el("h3", `flags-title ${kind}`, title));
  const list = el("ul", `flags ${kind}`);
  for (const flag of flags) {
    const li = el("li");
    li.append(el("span", "flag-point", flag.point || ""));
    if (flag.evidence) li.append(el("p", "flag-evidence", flag.evidence));
    list.append(li);
  }
  wrap.append(list);
  return wrap;
}

function renderAnalyst(analyst) {
  const section = el("section", "card analyst");
  const head = el("div", "analyst-head");
  head.append(el("h2", "section-title", "AI analyst"));
  head.append(el("span", "analyst-score", `${analyst.fraud_score ?? 0}/100 fraud`));
  section.append(head);
  if (analyst.verdict) {
    section.append(el("p", "analyst-verdict muted", String(analyst.verdict).replace(/_/g, " ")));
  }
  if (analyst.summary) section.append(el("p", "analyst-summary", analyst.summary));
  if (analyst.red_flags?.length) section.append(renderFlags("Red flags", analyst.red_flags, "red"));
  if (analyst.green_flags?.length) {
    section.append(renderFlags("Green flags", analyst.green_flags, "green"));
  }
  if (analyst.what_to_do?.length) {
    section.append(el("h3", "flags-title", "What to do"));
    const list = el("ul", "checklist");
    for (const item of analyst.what_to_do) list.append(el("li", "", item));
    section.append(list);
  }
  if (analyst.sources_used?.length) {
    section.append(el("h3", "flags-title", "Sources"));
    const list = el("ul", "analyst-sources");
    for (const url of analyst.sources_used.slice(0, 10)) {
      const li = el("li");
      const a = el("a", "", url);
      a.href = url;
      a.target = "_blank";
      a.rel = "noreferrer noopener";
      li.append(a);
      list.append(li);
    }
    section.append(list);
  }
  return section;
}

function renderChecklist(items) {
  const section = el("section", "card");
  section.append(el("h2", "section-title", "What to verify before applying"));
  const list = el("ul", "checklist");
  for (const item of items) list.append(el("li", "", item));
  section.append(list);
  return section;
}

function renderTrust(score, signals) {
  const section = el("section", "card");
  section.append(el("h2", "section-title", `Positive signals · trust ${score ?? 0}/100`));
  const list = el("ul", "trust");
  for (const signal of signals) {
    const li = el("li", "trust-item");
    li.append(el("span", "trust-points", `+${signal.points}`), el("strong", "", signal.label));
    if (signal.explanation) li.append(el("p", "trust-explanation", signal.explanation));
    list.append(li);
  }
  section.append(list);
  return section;
}

function renderSignals(signals) {
  const section = el("section", "card");
  section.append(el("h2", "section-title", `Detected signals (${signals.length})`));
  const list = el("ul", "signals");
  for (const signal of signals) {
    const li = el("li", `signal sev-${signal.severity}`);
    const label = el("span", "signal-label", signal.label);
    const points = el("span", "signal-points", `+${Math.round(signal.points)}`);
    li.append(points, label);
    li.append(el("p", "signal-explanation", signal.explanation));
    list.append(li);
  }
  section.append(list);
  return section;
}

function renderFindings(findings) {
  const section = el("section", "card");
  section.append(el("h2", "section-title", "What the investigation found"));
  const list = el("ul", "findings");
  for (const finding of findings) list.append(el("li", "", finding));
  section.append(list);
  return section;
}

function renderEvidence(evidence, pagesCaptured) {
  const section = el("section", "card");
  const title = pagesCaptured
    ? `Supporting evidence (${pagesCaptured} pages read)`
    : "Supporting evidence";
  section.append(el("h2", "section-title", title));

  const list = el("ul", "evidence");
  for (const item of evidence.slice(0, 12)) {
    const li = el("li");
    if (item.source_domain) li.append(el("div", "domain", item.source_domain));
    if (item.title) li.append(el("strong", "", item.title));
    if (item.summary) li.append(el("p", "", item.summary));
    if (item.source_url) {
      const a = el("a", "", item.source_url);
      a.href = item.source_url;
      a.target = "_blank";
      a.rel = "noreferrer noopener";
      li.append(a);
    }
    list.append(li);
  }
  section.append(list);
  return section;
}

// -------------------------------------------------------------- reporting ---
// Reporting writes about a named business, so the API requires a signed-in
// user. The extension gets its session from the website: the panel opens
// `<site>/?ext=<extension id>#/profile`, the user signs in there with Google,
// and the page pushes the token back here (see background.js).

function readStoredSession(raw) {
  if (!raw || !raw.token || !raw.user?.id) return null;
  return { token: raw.token, user: raw.user, receivedAt: raw.receivedAt || 0 };
}

/** The page to open for sign-in, carrying this extension's id for the handoff. */
function signInUrl() {
  const base = (settings.siteUrl || DEFAULT_SETTINGS.siteUrl).replace(/\/+$/, "");
  return `${base}/?ext=${chrome.runtime.id}#/profile`;
}

function openSignIn(status) {
  chrome.tabs.create({ url: signInUrl() });
  if (status) {
    status.textContent =
      "Finish signing in with Google in the tab that just opened — this panel updates by itself.";
  }
}

function authHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${session.token}`,
  };
}

/** Any 401 means the minted token is gone; ask for a new one. */
function markSessionStale() {
  sessionStale = true;
  renderAccount();
}

function renderReportAction() {
  const section = el("section", "card report-action");
  section.append(el("h2", "section-title", "Spotted a scam?"));

  const button = el("button", "report");
  button.type = "button";
  const status = el("p", "report-status muted");

  const signedIn = Boolean(session) && !sessionStale;
  if (signedIn) {
    section.append(
      el(
        "p",
        "muted",
        "Report this posting. It is stored against the company under your account, so repeat offenders surface for everyone and you can withdraw it later.",
      ),
    );
    button.textContent = "Report as scam";
    button.addEventListener("click", () => reportScam(button, status));
  } else {
    section.append(
      el(
        "p",
        "muted",
        session
          ? "Your sign-in has expired. Sign in again to report — the posting will still be here."
          : "Reporting needs a sign-in, so a report can be attributed to you and withdrawn later. Investigating stays anonymous and free.",
      ),
    );
    button.textContent = session ? "Sign in again" : "Sign in to report";
    button.addEventListener("click", () => {
      openSignIn(status);
      status.textContent = "Waiting for sign-in…";
    });
  }

  section.append(button, status);
  return section;
}

async function reportScam(button, status) {
  const text = lastRun.text.trim();
  if (text.length < 3) {
    status.textContent = "Nothing to report.";
    return;
  }
  button.disabled = true;
  status.textContent = "Sending…";
  try {
    const response = await fetch(`${lastRun.apiBase}/reports`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        text,
        report_type: "scam",
        source: "extension_user",
        risk_level: lastRun.riskLevel || null,
        risk_score: lastRun.riskScore ?? null,
      }),
    });
    if (response.status === 401) {
      markSessionStale();
      throw new Error("Sign-in expired — press “Sign in again” above.");
    }
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    button.textContent = "Reported ✓";
    status.textContent =
      "Thanks — stored. Open “My reports” to track the decision or withdraw it.";
    refreshMyReports();
  } catch (err) {
    button.disabled = false;
    status.textContent = `Could not report: ${err?.message || err}`;
  }
}

// ---------------------------------------------------------------- account ---
function renderAccount() {
  if (!session) {
    hide(els.account);
    return;
  }
  show(els.account);
  els.accountEmail.textContent = session.user.email || session.user.name || "signed in";
  if (sessionStale) {
    els.accountEmail.textContent += " · sign-in expired";
  }
}

async function refreshMyReports() {
  if (!session || els.reports.hidden) return;
  els.reports.replaceChildren(el("p", "muted", "Loading…"));
  try {
    const response = await fetch(`${settings.apiBase}/reports/mine`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (response.status === 401) {
      markSessionStale();
      els.reports.replaceChildren(el("p", "muted", "Sign in again to load your reports."));
      return;
    }
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    renderMyReports(await response.json());
  } catch (err) {
    els.reports.replaceChildren(el("p", "muted", `Could not load reports: ${err?.message || err}`));
  }
}

function renderMyReports(data) {
  const reports = data?.reports || [];
  els.reports.replaceChildren();
  if (!reports.length) {
    els.reports.append(el("p", "muted", "You have not reported anything yet."));
    return;
  }
  const list = el("ul", "report-list");
  for (const report of reports) {
    const li = el("li", "report-item");
    const head = el("div", "report-item-head");
    head.append(el("strong", "", report.company_name || "Unnamed company"));
    const chip = el("span", `report-chip chip-${report.status}`, report.status);
    head.append(chip);
    li.append(head);
    li.append(el("p", "report-item-meta muted", new Date(report.created_at).toLocaleDateString()));
    if (report.review_note) li.append(el("p", "report-item-note muted", report.review_note));
    if (report.status === "pending" || report.status === "approved") {
      const withdraw = el("button", "ghost", "Withdraw");
      withdraw.type = "button";
      withdraw.addEventListener("click", () => withdrawReport(report.id, withdraw));
      li.append(withdraw);
    }
    list.append(li);
  }
  els.reports.append(list);
}

async function withdrawReport(id, button) {
  button.disabled = true;
  button.textContent = "Withdrawing…";
  try {
    const response = await fetch(`${settings.apiBase}/reports/${encodeURIComponent(id)}/withdraw`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (response.status === 401) {
      markSessionStale();
      throw new Error("Sign-in expired — sign in again.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${response.status})`);
    }
    await refreshMyReports();
  } catch (err) {
    button.disabled = false;
    button.textContent = `Could not withdraw: ${err?.message || err}`;
  }
}

els.reportsToggle.addEventListener("click", () => {
  els.reports.hidden = !els.reports.hidden;
  if (!els.reports.hidden) refreshMyReports();
});

els.signOut.addEventListener("click", async () => {
  session = null;
  sessionStale = false;
  await chrome.storage.local.remove(SESSION_KEY);
  hide(els.reports);
  renderAccount();
  if (!els.result.hidden) {
    // Re-render the verdict so the report action reflects the sign-out.
    renderResult(lastResult, lastPagesCaptured);
  }
});

function colorVar(level) {
  return { LOW: "good", MODERATE: "warn", HIGH: "high", CRITICAL: "danger" }[level] || "muted";
}

// --------------------------------------------------------------- helpers ----
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function show(node) {
  node.hidden = false;
}
function hide(node) {
  node.hidden = true;
}

function setProgress(phase, note) {
  els.progressPhase.textContent = phase;
  els.progressNote.textContent = note;
}

// A bare "Failed to fetch" tells the user nothing, so network-shaped failures
// get an actionable message pointing at the one thing they can fix.
const UNREACHABLE = /failed to fetch|load failed|network|econnrefused|err_connection/i;

function showError(message) {
  const text = String(message ?? "");
  const unreachable = UNREACHABLE.test(text);
  els.error.replaceChildren(
    el("strong", "", unreachable ? "Cannot reach the backend" : "Something went wrong"),
  );
  els.error.append(
    document.createTextNode(
      unreachable
        ? `The extension could not reach ${settings.apiBase}. Start the backend, then check the API base in Settings.`
        : text,
    ),
  );
  show(els.error);
  // Bring the guidance back if there is no result to look at.
  if (els.result.hidden) show(els.empty);
}

// ------------------------------------------------------------------ boot ----
initTheme();

// The website writes the handoff session straight into storage, so the panel
// learns about a fresh sign-in without polling and without a message race.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[SESSION_KEY]) return;
  session = readStoredSession(changes[SESSION_KEY].newValue);
  if (session) sessionStale = false;
  renderAccount();
  if (!els.reports.hidden) refreshMyReports();
  if (lastResult && !els.result.hidden) renderResult(lastResult, lastPagesCaptured);
});

loadSettings().then(() => {
  refreshQuota();
  renderAccount();
  chrome.storage.local.get(["lastInput"]).then(({ lastInput }) => {
    if (lastInput) els.input.value = lastInput;
  });
});
