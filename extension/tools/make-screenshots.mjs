/**
 * Generates the Chrome Web Store screenshots (1280 x 800), with no image
 * dependencies.
 *
 *   node tools/make-screenshots.mjs
 *   node tools/make-screenshots.mjs --out store/screenshots
 *
 * Each frame is 1280 x 800 of on-brand copy next to a mock of the real side
 * panel. The panel markup below mirrors `sidepanel.html` + the render functions
 * in `sidepanel.js`, and it is styled by the *real* `sidepanel.css`, so the
 * screenshots cannot drift from the product's look. The analysis content is
 * illustrative sample data (a synthetic posting from the test fixtures), not a
 * recorded run — see store/screenshots/README.md.
 *
 * Rendering is done by local Chrome, so the requirement is just "Chrome is
 * installed"; nothing is downloaded. Set CHROME_PATH to override the lookup.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const EXT_DIR = resolve(HERE, "..");
const ROOT = resolve(EXT_DIR, "..");

const WIDTH = 1280;
const HEIGHT = 800;
// Headless Chrome sizes the *window*, not the viewport, so it is asked for more
// than is needed and the frame is cropped back out of the top-left corner.
const WINDOW = [WIDTH + 200, HEIGHT + 200];

/* ------------------------------------------------------------- arguments -- */

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const OUT_DIR = isAbsolute(arg("out", "store/screenshots"))
  ? arg("out", "store/screenshots")
  : resolve(ROOT, arg("out", "store/screenshots"));

/* ----------------------------------------------------------------- chrome -- */

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    `Chrome not found. Set CHROME_PATH to the browser binary.\nTried:\n  ${CHROME_CANDIDATES.join("\n  ")}`,
  );
}

/* --------------------------------------------------------------- palette --- */

const INK = "#14110c";
const PAGE = "#f3eee4";
const PANEL = "#fdfbf7";
const PANEL_2 = "#f6f2ea";
const LINE = "#e1d8c7";
const SIGNAL = "#c2410c";

const FONT_DISPLAY = '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif';
const FONT_SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
const FONT_MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

/* --------------------------------------------------------- panel fragments - */

const MARK = `<svg class="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="11" cy="11" r="7.25" stroke="currentColor" stroke-width="1.75" />
          <path d="M14.6 14.6 20 20" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" />
          <path d="M7.6 11h6.8" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" />
        </svg>`;

const TOPBAR = `<header class="topbar">
      <div class="brand">${MARK}<h1>Fakee</h1></div>
      <button class="icon-button" type="button" aria-label="Toggle theme">\u263E</button>
    </header>`;

const PLACEHOLDER = [
  "Company: ABC Technologies",
  "Internship: Software Development Intern",
  "They contacted me on WhatsApp. Selected without an interview.",
  "Asked for a \u20b91,500 registration fee. Website: abc-careers.xyz",
].join("&#10;");

/**
 * The first lines of SYN-001 from backend/tests/fixtures/seed_posts.json, which
 * is the kind of posting the tool exists to catch. Trimmed so the whole paste
 * fits the panel's textarea without it scrolling.
 */
const PASTE = `URGENT HIRING - WORK FROM HOME
Data Entry / Form Filling Job. No experience needed. 12th pass / any graduate.
Salary: Rs 35,000 per month (only 2-3 hours/day)
Limited seats - only 20 positions left!
Pay Rs 1,500 refundable registration fee to confirm your seat. Refund after first salary.`;

/** Textarea height that shows the whole paste without an inner scrollbar. */
const PASTE_HEIGHT = 172;

function inputCard({ value = "", disabled = false, settings = false, minHeight = null }) {
  const style = minHeight ? ` style="min-height:${minHeight}px"` : "";
  return `<section class="card">
        <label class="label" for="input">Job / internship details</label>
        <textarea id="input"${style}${
          settings ? ' placeholder="' + PLACEHOLDER + '"' : ""
        }>${value}</textarea>
        <details class="settings">
          <summary>Settings</summary>
          <label class="label" for="api-base">Backend API base</label>
          <input id="api-base" type="text" spellcheck="false" value="https://fakee-pa1j.onrender.com/api" />
          <label class="label" for="provider">Search provider</label>
          <select id="provider">
            <option value="duckduckgo">DuckDuckGo (free, no key)</option>
            <option value="brave">Brave Search API (free tier)</option>
          </select>
          <label class="label" for="brave-key">Brave API key (optional)</label>
          <input id="brave-key" type="password" spellcheck="false" placeholder="only if using Brave" />
          <label class="checkbox-row" for="include-analyst">
            <input id="include-analyst" type="checkbox" checked />
            Include AI analyst (a few more tokens; off = frugal)
          </label>
        </details>
        <button id="run" class="primary" type="button"${disabled ? " disabled" : ""}>Investigate</button>
        <p class="hint">Ctrl / \u2318 + Enter to investigate</p>
      </section>`;
}

const EMPTY_STATE = `<section id="empty" class="empty">
        <h2>Check a posting before you trust it</h2>
        <p>
          Searches and page reads run in your own browser, which keeps the tool free and private.
          Paste whatever you have \u2014 missing details are fine.
        </p>
        <ol>
          <li>Paste the message, offer or job description.</li>
          <li>Press Investigate and watch the pages it reads.</li>
          <li>Read the verdict, the signals and the sources.</li>
        </ol>
      </section>`;

const PROGRESS = `<section id="progress" class="progress" aria-live="polite">
        <div class="spinner"></div>
        <p class="progress-phase">Reading pages \u00b7 7/12</p>
        <p class="progress-note muted">11 pages found \u00b7 9 readable</p>
      </section>`;

/**
 * Mirrors renderVerdict() in sidepanel.js. The real code sets the score colour
 * from the level with inline JS, so it is inlined here too.
 */
const VERDICT = `<div class="verdict level-HIGH">
        <div class="score-row">
          <span class="score" style="color:var(--high)">87</span>
          <span class="score-max">/100</span>
          <span class="level-tag level-HIGH">HIGH RISK</span>
        </div>
        <div class="status-row">
          <span class="status-chip status-NEEDS_VERIFICATION">Needs verification</span>
        </div>
        <h2 class="headline">Advance fee, WhatsApp-only contact, and a 19-day-old domain</h2>
        <p class="summary">
          The posting asks for a \u201crefundable\u201d \u20b91,500 registration fee before any interview,
          routes every reply through a personal WhatsApp number, and names no registered entity.
          Three independent money-request patterns matched.
        </p>
        <div class="recommendation">
          <strong>What to do</strong>
          <p>
            Do not pay. Verify the employer through the company's own website and its MCA
            registration \u2014 through a channel you initiate, not one they sent you.
          </p>
        </div>
      </div>`;

/** Mirrors renderSignals() in sidepanel.js. */
const SIGNALS = [
  {
    sev: "critical",
    points: 22,
    label: "Upfront fee requested before hiring",
    explanation:
      "\u201cPay Rs 1,500 refundable registration fee to confirm your seat.\u201d No genuine employer charges to hire.",
  },
  {
    sev: "high",
    points: 18,
    label: "Messaging-app-only contact channel",
    explanation:
      "Every reply is routed to a personal WhatsApp number, with no company domain, mailbox or landline anywhere in the posting.",
  },
  {
    sev: "high",
    points: 16,
    label: "Sensitive documents requested up front",
    explanation:
      "Aadhaar, PAN and bank details are asked for before any interview, offer letter or identity check.",
  },
  {
    sev: "medium",
    points: 12,
    label: "Domain registered recently",
    explanation:
      "The only linked domain resolves to a registration 19 days old, behind a privacy-shielded WHOIS record.",
  },
  {
    sev: "medium",
    points: 11,
    label: "Unrealistic compensation or hours",
    explanation:
      "\u20b935,000 a month for two to three hours a day of unskilled data entry, with no interview.",
  },
  {
    sev: "medium",
    points: 8,
    label: "Manufactured urgency",
    explanation:
      "\u201cLimited seats \u2014 only 20 positions left\u201d with a same-day deadline to pay.",
  },
];

function signalsCard() {
  const items = SIGNALS.map(
    (s) => `<li class="signal sev-${s.sev}">
            <span class="signal-points">+${s.points}</span>
            <span class="signal-label">${s.label}</span>
            <p class="signal-explanation">${s.explanation}</p>
          </li>`,
  ).join("\n          ");
  return `<section class="card">
        <h2 class="section-title">Detected signals (${SIGNALS.length})</h2>
        <ul class="signals">
          ${items}
        </ul>
      </section>`;
}

/** Mirrors renderCoverage() in sidepanel.js. */
const COVERAGE = `<section class="card coverage">
        <div class="coverage-head">
          <h2 class="section-title">Evidence coverage</h2>
          <span class="coverage-status muted">sufficient to judge</span>
        </div>
        <div class="coverage-row">
          <div class="coverage-stat"><span class="coverage-value">12</span><span class="coverage-label">Pages captured</span></div>
          <div class="coverage-stat"><span class="coverage-value">9</span><span class="coverage-label">Sent to model</span></div>
          <div class="coverage-stat"><span class="coverage-value">3</span><span class="coverage-label">Rules-only</span></div>
          <div class="coverage-stat"><span class="coverage-value">14</span><span class="coverage-label">Signal sentences</span></div>
        </div>
        <p class="coverage-note muted">Pages that only discuss job scams in general \u2014 and never mention this company \u2014 were set aside, not counted.</p>
      </section>`;

/** Mirrors renderEvidence() in sidepanel.js. */
const EVIDENCE = [
  {
    domain: "mca.gov.in",
    title: "No matching registered entity",
    summary:
      "A registry search for the company name returned no active Indian registration under that spelling.",
    url: "https://www.mca.gov.in/",
  },
  {
    domain: "cybercrime.gov.in",
    title: "Advisory: recruitment-fee fraud",
    summary:
      "Official advisory warning against employers who ask for a registration fee, deposit or training cost before hiring.",
    url: "https://cybercrime.gov.in/",
  },
  {
    domain: "icann.org",
    title: "Domain registration lookup",
    summary:
      "The only linked domain was created 19 days before this check, with the registrant contact shielded.",
    url: "https://lookup.icann.org/",
  },
];

function evidenceCard() {
  const items = EVIDENCE.map(
    (e) => `<li>
            <div class="domain">${e.domain}</div>
            <strong>${e.title}</strong>
            <p>${e.summary}</p>
            <a href="${e.url}">${e.url}</a>
          </li>`,
  ).join("\n          ");
  return `<section class="card">
        <h2 class="section-title">Supporting evidence (12 pages read)</h2>
        <ul class="evidence">
          ${items}
        </ul>
      </section>`;
}

const FOOTER = `<footer class="footer">Never pay for a job, internship or \u201ctraining\u201d. Report fraud on cybercrime.gov.in or 1930.</footer>`;

/* --------------------------------------------------------------- the shots - */

const SHOTS = [
  {
    name: "01-check-a-posting",
    eyebrow: "Fakee \u00b7 Chrome extension",
    headline: "Check a job offer before you trust it.",
    lede: "Paste the message, the offer letter, or just the company name. Fakee runs the searches, reads the result pages, and comes back with a verdict and the evidence behind it.",
    chips: ["Runs in your browser", "Explainable score", "No account needed"],
    panel: [TOPBAR, inputCard({ settings: true }), EMPTY_STATE, FOOTER].join("\n"),
  },
  {
    name: "02-paste-what-you-have",
    eyebrow: "Step 01 \u00b7 Paste",
    headline: "Give it whatever you have.",
    lede: "A forwarded message, an offer letter, a job description, a link. Missing details are fine \u2014 it works on partial text and tells you which facts it could not confirm.",
    chips: ["Company \u00b7 role \u00b7 pay", "Channel \u00b7 fee", "Partial text is fine"],
    panel: [
      TOPBAR,
      inputCard({ value: PASTE, minHeight: PASTE_HEIGHT }),
      EMPTY_STATE,
      FOOTER,
    ].join("\n"),
  },
  {
    name: "03-searches-run-in-your-browser",
    eyebrow: "Step 02 \u00b7 Investigate",
    headline: "The searches run in your own browser.",
    lede: "Fakee opens the searches and reads the result pages on your machine \u2014 not on a server's search quota. Only the captured page text is sent for analysis.",
    chips: ["Complaints", "Reviews", "Domain records", "News"],
    panel: [
      TOPBAR,
      inputCard({ value: PASTE, disabled: true, minHeight: PASTE_HEIGHT }),
      PROGRESS,
      FOOTER,
    ].join("\n"),
  },
  {
    name: "04-a-verdict-you-can-argue-with",
    eyebrow: "Step 03 \u00b7 The verdict",
    headline: "A score you can argue with.",
    lede: "Deterministic rules produce the number \u2014 the model reads and summarises, it does not decide. Every point is attributed to a named pattern and the text that triggered it.",
    chips: ["29 patterns", "Same posting, same result", "Severity per signal"],
    panel: [TOPBAR, VERDICT, signalsCard(), FOOTER].join("\n"),
  },
  {
    name: "05-shows-what-it-read",
    eyebrow: "Step 04 \u00b7 The evidence",
    headline: "It shows what it read \u2014 and what it could not.",
    lede: "How many pages were captured, how many reached the model, and which sources carried a real signal. When the evidence is thin, it says so instead of guessing.",
    chips: ["No black box", "Sources listed", "\u201cInsufficient evidence\u201d is an answer"],
    panel: [TOPBAR, COVERAGE, evidenceCard(), FOOTER].join("\n"),
  },
];

/* ----------------------------------------------------------------- render -- */

const PANEL_CSS = readFileSync(join(EXT_DIR, "sidepanel.css"), "utf8");

function panelDoc(inner) {
  return `<!doctype html>
<html lang="en" data-theme="light">
  <head>
    <meta charset="UTF-8" />
    <style>
${PANEL_CSS}
    </style>
    <style>
      html { scrollbar-width: none; }
      ::-webkit-scrollbar { display: none; }
      body { width: 400px; }
    </style>
  </head>
  <body>
    ${inner}
  </body>
</html>`;
}

function frameDoc(panelFile, shot) {
  const chips = shot.chips
    .map((chip) => `<li>${chip}</li>`)
    .join("\n            ");
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <style>
      :root {
        --page: ${PAGE};
        --panel: ${PANEL};
        --panel-2: ${PANEL_2};
        --ink: ${INK};
        --muted: #6f6757;
        --faint: #9a9182;
        --line: ${LINE};
        --signal: ${SIGNAL};
        --font-sans: ${FONT_SANS};
        --font-display: ${FONT_DISPLAY};
        --font-mono: ${FONT_MONO};
      }
      * { box-sizing: border-box; }
      html, body { margin: 0; padding: 0; width: ${WIDTH}px; height: ${HEIGHT}px; overflow: hidden; }
      body { background: var(--page); color: var(--ink); font-family: var(--font-sans); position: relative; }
      body::before {
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        background:
          radial-gradient(760px 480px at 20% 14%, rgba(194, 65, 12, 0.075), transparent 70%),
          radial-gradient(680px 420px at 90% 94%, rgba(20, 17, 12, 0.05), transparent 72%);
      }
      .stage { position: relative; display: flex; align-items: center; height: ${HEIGHT}px; }

      .copy { width: 740px; flex: none; padding: 0 76px 0 76px; }
      .eyebrow {
        margin: 0 0 16px;
        font-family: var(--font-mono);
        font-size: 11px;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--signal);
        font-weight: 600;
      }
      .copy h1 {
        margin: 0;
        font-family: var(--font-display);
        font-size: 50px;
        line-height: 1.08;
        letter-spacing: -0.022em;
        font-weight: 600;
        /* Keep a wrapped headline even instead of orphaning its last word. */
        text-wrap: balance;
      }
      .lede {
        margin: 20px 0 0;
        max-width: 560px;
        font-size: 16.5px;
        line-height: 1.62;
        color: var(--muted);
      }
      .chips {
        list-style: none;
        margin: 26px 0 0;
        padding: 0;
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      .chips li {
        font-family: var(--font-mono);
        font-size: 10.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
        border: 1px solid var(--line);
        background: rgba(253, 251, 247, 0.72);
        border-radius: 999px;
        padding: 6px 11px;
      }

      .device {
        width: 482px;
        margin: 0 auto;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: var(--panel);
        overflow: hidden;
        box-shadow: 0 20px 44px rgba(20, 17, 12, 0.11), 0 2px 6px rgba(20, 17, 12, 0.06);
      }
      .device-bar {
        display: flex;
        align-items: center;
        gap: 9px;
        height: 36px;
        padding: 0 12px;
        background: var(--panel-2);
        border-bottom: 1px solid var(--line);
      }
      .dots { display: flex; gap: 5px; }
      .dots i { width: 8px; height: 8px; border-radius: 50%; background: var(--line); display: block; }
      .device-title {
        margin-left: 2px;
        font-size: 11px;
        color: var(--faint);
        font-family: var(--font-mono);
        letter-spacing: 0.04em;
      }
      .device-body { height: 720px; background: var(--page); }
      .device-body iframe {
        display: block;
        border: 0;
        width: 400px;
        height: 600px;
        transform: scale(1.2);
        transform-origin: 0 0;
      }
    </style>
  </head>
  <body>
    <div class="stage">
      <div class="copy">
        <p class="eyebrow">${shot.eyebrow}</p>
        <h1>${shot.headline}</h1>
        <p class="lede">${shot.lede}</p>
        <ul class="chips">
            ${chips}
        </ul>
      </div>
      <div class="device">
        <div class="device-bar">
          <span class="dots"><i></i><i></i><i></i></span>
          <span class="device-title">Fakee \u2014 side panel</span>
        </div>
        <div class="device-body"><iframe src="${panelFile}" title="Fakee side panel"></iframe></div>
      </div>
    </div>
  </body>
</html>`;
}

/* ------------------------------------------------------------------ png ---- */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

function encodePng(width, height, rgba) {
  const raw = Buffer.alloc(height * (width * 4 + 1));
  let offset = 0;
  for (let y = 0; y < height; y += 1) {
    raw[offset] = 0; // filter: none
    offset += 1;
    rgba.copy(raw, offset, y * width * 4, (y + 1) * width * 4);
    offset += width * 4;
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 6; // colour type: RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function pngSize(buffer) {
  if (buffer.toString("ascii", 1, 4) !== "PNG") throw new Error("not a PNG");
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

/**
 * Chrome writes non-interlaced, filter-typed truecolour PNGs, which is enough
 * for a coarse "did anything actually render?" check: inflate the IDAT stream
 * and undo the per-row filters.
 */
function decodePng(buffer) {
  const { width, height } = pngSize(buffer);
  const idat = [];
  let offset = 8;
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    if (type === "IDAT") idat.push(buffer.subarray(offset + 8, offset + 8 + length));
    offset += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(idat));

  const bpp = 4; // Chrome writes RGBA8 here.
  const stride = width * bpp;
  const out = Buffer.alloc(height * stride);
  let pos = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[pos];
    pos += 1;
    const row = raw.subarray(pos, pos + stride);
    pos += stride;
    const prev = y > 0 ? out.subarray((y - 1) * stride, y * stride) : null;
    const cur = out.subarray(y * stride, (y + 1) * stride);
    for (let x = 0; x < stride; x += 1) {
      const a = x >= bpp ? cur[x - bpp] : 0;
      const b = prev ? prev[x] : 0;
      const c = prev && x >= bpp ? prev[x - bpp] : 0;
      let value = row[x];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        value += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      cur[x] = value & 0xff;
    }
  }
  return { width, height, pixels: out };
}

/** Copies a rectangle out of a decoded image into a fresh RGBA buffer. */
function crop(image, x0, y0, w, h) {
  if (x0 + w > image.width || y0 + h > image.height) {
    throw new Error(`crop ${w}x${h} at ${x0},${y0} exceeds ${image.width}x${image.height}`);
  }
  const out = Buffer.alloc(w * h * 4);
  for (let y = 0; y < h; y += 1) {
    const from = ((y0 + y) * image.width + x0) * 4;
    image.pixels.copy(out, y * w * 4, from, from + w * 4);
  }
  return { width: w, height: h, pixels: out };
}

const hex = (value) => [
  parseInt(value.slice(1, 3), 16),
  parseInt(value.slice(3, 5), 16),
  parseInt(value.slice(5, 7), 16),
];

/** Scans a rectangle and returns the fraction of pixels passing `test`. */
function measure(image, rect, test) {
  const { x0, y0, x1, y1 } = rect;
  let hits = 0;
  let total = 0;
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const i = (y * image.width + x) * 4;
      total += 1;
      if (test(image.pixels[i], image.pixels[i + 1], image.pixels[i + 2])) hits += 1;
    }
  }
  return total ? hits / total : 0;
}

const isNear = (target, tolerance) => {
  const [tr, tg, tb] = hex(target);
  return (r, g, b) =>
    Math.abs(r - tr) <= tolerance && Math.abs(g - tg) <= tolerance && Math.abs(b - tb) <= tolerance;
};

/**
 * Anything noticeably darker than a panel/card/textarea background: real text in
 * any of the palette's ink, muted or faint tones, plus the orange button. Card
 * fills (luminance ~250) and hairline borders (~215) are excluded, so this
 * measures "is there type here", not "is there a box here".
 */
const isInk = (r, g, b) => 0.299 * r + 0.587 * g + 0.114 * b < 200;

/** The burnt-orange accent, including the half-opacity disabled button. */
const isAccent = (r, g, b) => r > 150 && r - b > 55 && r - g > 25;

/**
 * A coarse luminance map, for eyeballing a render without opening the PNG.
 * Each cell is the mean of the block it covers, so lines of type show up as
 * bands rather than as scattered pixels.
 */
function asciiPreview(image, rect, cols = 100, rows = 56) {
  const ramp = " .:-=+*#%@";
  const cellW = (rect.x1 - rect.x0) / cols;
  const cellH = (rect.y1 - rect.y0) / rows;
  const lines = [];
  for (let row = 0; row < rows; row += 1) {
    let line = "";
    for (let col = 0; col < cols; col += 1) {
      const x0 = Math.floor(rect.x0 + cellW * col);
      const x1 = Math.max(x0 + 1, Math.floor(rect.x0 + cellW * (col + 1)));
      const y0 = Math.floor(rect.y0 + cellH * row);
      const y1 = Math.max(y0 + 1, Math.floor(rect.y0 + cellH * (row + 1)));
      let sum = 0;
      let n = 0;
      for (let y = y0; y < y1; y += 1) {
        for (let x = x0; x < x1; x += 1) {
          const i = (y * image.width + x) * 4;
          sum += 0.299 * image.pixels[i] + 0.587 * image.pixels[i + 1] + 0.114 * image.pixels[i + 2];
          n += 1;
        }
      }
      const lum = sum / n;
      const level = Math.min(9, Math.max(0, Math.round(((255 - lum) / 255) * 9)));
      line += ramp[level];
    }
    lines.push(line);
  }
  return lines.join("\n");
}

/** The row bands a panel is divided into, for reporting where content sits. */
function bandProfile(image, rect, bands = 10) {
  const height = rect.y1 - rect.y0;
  const out = [];
  for (let b = 0; b < bands; b += 1) {
    const y0 = rect.y0 + Math.floor((height * b) / bands);
    const y1 = rect.y0 + Math.floor((height * (b + 1)) / bands);
    out.push(measure(image, { ...rect, y0, y1 }, isInk));
  }
  return out;
}

/* ---------------------------------------------------------------- layout -- */

// Geometry of the rendered frame, used both to draw and to verify. The copy
// column is a fixed 740px; the device is centred in what is left of the 1280.
const COPY_RECT = { x0: 80, y0: 120, x1: 660, y1: 700 };
const PANEL_RECT = { x0: 772, y0: 62, x1: 1248, y1: 776 };

/* ------------------------------------------------------------------- main -- */

async function main() {
  const chrome = findChrome();
  mkdirSync(OUT_DIR, { recursive: true });
  const workspace = join(tmpdir(), `fakee-screenshots-${process.pid}`);
  const profile = join(workspace, "chrome-profile");
  rmSync(workspace, { recursive: true, force: true });
  rmSync(profile, { recursive: true, force: true });
  mkdirSync(workspace, { recursive: true });

  const problems = [];
  const written = [];

  try {
    for (const shot of SHOTS) {
      const panelFile = `${shot.name}.panel.html`;
      const frameFile = `${shot.name}.frame.html`;
      writeFileSync(join(workspace, panelFile), panelDoc(shot.panel), "utf8");
      writeFileSync(join(workspace, frameFile), frameDoc(panelFile, shot), "utf8");

      const raw = join(workspace, `${shot.name}.raw.png`);
      const url = `file:///${join(workspace, frameFile).replace(/\\/g, "/")}`;
      execFileSync(
        chrome,
        [
          "--headless=new",
          "--disable-gpu",
          "--hide-scrollbars",
          // Greyscale antialiasing and sRGB keep the PNGs identical across
          // machines instead of picking up the host's ClearType fringes.
          "--disable-lcd-text",
          "--force-color-profile=srgb",
          "--force-device-scale-factor=1",
          "--no-first-run",
          "--no-default-browser-check",
          "--allow-file-access-from-files",
          `--user-data-dir=${profile}`,
          "--virtual-time-budget=4000",
          `--window-size=${WINDOW[0]},${WINDOW[1]}`,
          `--screenshot=${raw}`,
          url,
        ],
        { stdio: "pipe" },
      );

      const captured = decodePng(readFileSync(raw));
      if (captured.width < WIDTH || captured.height < HEIGHT) {
        problems.push(
          `${shot.name}: viewport ${captured.width}x${captured.height} is smaller than the ${WIDTH}x${HEIGHT} frame`,
        );
        continue;
      }

      // The page is a fixed 1280x800 body at the top-left of the viewport, so
      // the store image is exactly the top-left crop of the window.
      const image = crop(captured, 0, 0, WIDTH, HEIGHT);
      writeFileSync(join(OUT_DIR, `${shot.name}.png`), encodePng(WIDTH, HEIGHT, image.pixels));

      const colours = new Set();
      const copyInk = measure(image, COPY_RECT, isInk);
      const panelInk = measure(image, PANEL_RECT, isInk);
      const panelPaper = measure(image, PANEL_RECT, isNear(PANEL, 7));
      const copyAccent = measure(image, COPY_RECT, isAccent);
      const panelAccent = measure(image, PANEL_RECT, isAccent);
      for (let y = 0; y < HEIGHT; y += 40) {
        for (let x = 0; x < WIDTH; x += 40) {
          const i = (y * WIDTH + x) * 4;
          colours.add((image.pixels[i] << 16) | (image.pixels[i + 1] << 8) | image.pixels[i + 2]);
        }
      }

      if (colours.size < 50) problems.push(`${shot.name}: image looks blank (${colours.size} colours)`);
      if (copyInk < 0.02) {
        problems.push(`${shot.name}: headline column did not render (ink ${copyInk.toFixed(3)})`);
      }
      if (panelInk < 0.012) {
        problems.push(`${shot.name}: side panel has no text (ink ${panelInk.toFixed(3)})`);
      }
      if (panelPaper < 0.04) {
        problems.push(
          `${shot.name}: side panel not styled — iframe CSS missing (panel ${panelPaper.toFixed(3)})`,
        );
      }
      if (copyAccent < 0.0003) {
        problems.push(`${shot.name}: brand accent missing from the headline column`);
      }
      if (panelAccent < 0.0001) {
        problems.push(`${shot.name}: brand accent missing from the side panel`);
      }

      if (process.argv.includes("--preview")) {
        console.log(`\n--- ${shot.name} (side panel) ---\n${asciiPreview(image, PANEL_RECT)}`);
      }

      const bands = bandProfile(image, PANEL_RECT)
        .map((v) => (v * 100).toFixed(1))
        .join(" ");
      written.push(
        `  ${shot.name}.png  ${WIDTH}x${HEIGHT}  ink copy ${(copyInk * 100).toFixed(1)}% / panel ${(panelInk * 100).toFixed(1)}%` +
          `  accent ${(panelAccent * 100).toFixed(3)}%  panel bands ${bands}`,
      );
    }
  } finally {
    rmSync(workspace, { recursive: true, force: true });
    rmSync(profile, { recursive: true, force: true });
  }

  console.log(`Wrote ${written.length} screenshots to ${OUT_DIR}`);
  console.log(written.join("\n"));

  if (problems.length) {
    console.error(`\n${problems.length} problem(s):`);
    for (const problem of problems) console.error(`  - ${problem}`);
    process.exit(1);
  }
  console.log(`\nAll ${written.length} screenshots verified: exact ${WIDTH}x${HEIGHT}, headline + panel both rendered with the real stylesheet.`);
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
