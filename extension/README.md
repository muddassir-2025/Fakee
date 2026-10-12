# Fakee — Chrome extension

The extension is the **primary client**. It does the expensive, previously-paid
work — web search and page reading — **inside your own browser** (no search API
credits), then sends the captured pages to the backend, which does the
extraction, pattern detection and risk scoring.

## How it works

```
Side panel                 Offscreen crawler                     Backend
   │  paste text                │                                   │
   ├── RUN ────────────────────►│                                   │
   │                            ├── POST /api/queries ─────────────►│  (Groq extract)
   │                            │◄──── queries + structured input ──┤
   │                            │                                   │
   │                            │  search web (see providers)       │
   │                            │  fetch + extract each result page │
   │                            │                                   │
   │                            ├── POST /api/investigate/with-evidence ─►│  (patterns + risk)
   │◄──── PROGRESS / RESULT ────┤◄──── verdict ─────────────────────┤
```

- **Service worker** (`background.js`) — opens the side panel on toolbar click
  and ensures the offscreen document exists. It does **not** run the crawl
  (MV3 workers are killed after ~30s idle).
- **Offscreen document** (`offscreen.js`) — has a DOM (`DOMParser`) and a longer
  lifetime, so the crawl runs here: query planning, search discovery, page
  fetch and readable-text extraction.
- **Side panel** (`sidepanel.html/js/css`) — the UI: paste text, run, watch
  progress, read the verdict.

## Install (developer mode)

1. Start the backend (see the repo root README), e.g. `http://localhost:8000`.
2. Open `chrome://extensions`.
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select this `extension/` folder.
5. Click the toolbar icon to open the side panel (Chrome 116+).

The landing site (`frontend/`) also tells people to install it this way; once the
extension has a Chrome Web Store listing, set `EXTENSION.storeUrl` in
`frontend/src/content.ts` and the site will link to it instead — and, because the
site reads the extension id out of that URL, its own **Check a posting** panel
starts running investigations inline through the extension.

## UI notes

- On first open the panel shows a short empty state explaining the three steps;
  it is replaced by progress and then the verdict.
- **Ctrl / ⌘ + Enter** runs the investigation from the textarea.
- The panel follows the system light/dark preference (toggle in the top right).
- If the backend cannot be reached, the error names the API base it tried and
  points at Settings, rather than showing a bare “Failed to fetch”.
- Icons are generated from one source of truth:
  `node tools/make-icons.mjs` rewrites `icons/icon{16,32,48,128}.png` with no
  image dependencies.

### The free budget bar

One bar covers the free allowances a run can spend, read from `GET /api/budget`:

| Track | Window | Where the number comes from |
| --- | --- | --- |
| Groq requests | daily | the backend's own Groq response headers |
| Brave searches | monthly | the rate-limit headers of this browser's Brave calls |

- **Stored server-side, shared by everyone.** The allowances belong to the
deployment's one free account, so the bar neither resets when you close and
reopen the panel nor differs between users. It is one row per provider window in
the `provider_budgets` table (apply it with `alembic upgrade head`).
- **Each budget is its own track**, filled by its own remaining share, rather
than one blended percentage: a daily request quota and a monthly search
allowance are not comparable, and averaging them hides whichever is about to run
out.
- **Only the budgets in play are counted.** A DuckDuckGo run shows Groq alone;
Brave appears when Brave is selected, or when a self-hosted SearXNG instance can
fall back to it (i.e. a Brave key is set).
- Brave reports several windows at once — its `X-RateLimit-*` headers are
comma-separated per-window pairs — and the bar tracks the **monthly** one,
because the per-second burst limit says nothing about the allowance left.
- Per-minute figures (Groq tokens) are a footnote, not a track: they would make
the bar jump on every run without adding signal.
- Every number is one a provider actually reported. A window whose reset time has
passed counts as refilled; one older than its window with no reset time is shown
as `—` rather than carried forward as if it were current.

## Configuration

Open **Settings** in the side panel:

| Setting | Default | Notes |
| --- | --- | --- |
| Backend API base | `http://localhost:8000/api` | Where your FastAPI backend runs |
| Website URL | `http://localhost:5173` | The landing site; the panel signs in through it and the site hands the session back |
| Search provider | DuckDuckGo (free, no key) | DuckDuckGo needs no key; Brave, TinyFish and SERPdive each need their own free key; SearXNG needs your own instance |
| Brave API key | — | Optional; enables the Brave Search free tier (~1,000 searches/month of monthly credit) |
| TinyFish API key | — | Optional; free tier of 30 requests/minute and 12,000/day per key (resets 00:00 UTC) |
| SERPdive API key | — | Optional; runs on SERPdive's free `krill` model |
| SearXNG instance URL | — | Optional; base URL of your own instance, e.g. `http://localhost:8080` |

Only the selected provider's own fields are shown, so a key that is set but does
not apply to the current run is never mistaken for the one being used.

### Search providers

| Provider | Key | Notes |
| --- | --- | --- |
| DuckDuckGo | none | The default. Keyless and free, but its HTML endpoint is frequently blocked by anti-bot pages — see *Known limitations*. |
| Brave | yes | `api.search.brave.com`. Free tier is a $5/month credit, roughly 1,000 searches. Retries a `429` honouring `Retry-After`. |
| TinyFish | yes | `api.search.tinyfish.ai`, sent as `X-API-Key`. Free tier: 30 requests/minute, 12,000/day. Retries `429`/`502`/`503`. |
| SERPdive | yes | `POST api.serpdive.com/v1/search` with `Authorization: Bearer`, pinned to the free `krill` model. Retries `429`/`502`/`503`. |
| SearXNG | none | Your own instance; queries go to no third party. See below. |

TinyFish, SERPdive and SearXNG are queried **one at a time with a 700 ms gap**
(`GAP_MS` in `offscreen.js`), because each is capped per key or backed by a
single machine. DuckDuckGo and Brave keep their concurrent burst.

**Only SearXNG chains onto a fallback** (`searxng → brave (if a key is set) →
duckduckgo`), because a self-hosted instance is the most likely to be down or
misconfigured; the panel shows a non-blocking notice naming what it used
instead. **Brave, TinyFish and SERPdive are used exactly as selected** — a run
answered by a provider the user did not pick was more confusing than a clear
failure, and a failed provider is reported (with its own error: bad key, quota,
rate limit) rather than silently substituted.

The backend must allow the extension origin. It does by default via
`CORS_ORIGIN_REGEX=chrome-extension://.*`.

### Self-hosted SearXNG

Selecting **Self-Hosted SearXNG** searches *your* instance instead of a public
engine, so no third party sees the queries. Two things must hold:

1. **JSON output is enabled.** SearXNG disables it by default — add `json` to
   `search.formats` in the instance's `settings.yml` and restart it:

   ```yaml
   search:
     formats:
       - html
       - json
   ```

2. **The instance is reachable from the browser.** `http://localhost:8080` and
   `http://127.0.0.1:8080` work as-is. For a remote instance prefer `https://`,
   because a plain-`http` LAN address can be blocked as mixed content.

**No CORS or manifest change is needed.** The extension already declares
`<all_urls>` host permission — it must read arbitrary result pages — which also
covers whatever instance URL you enter, including `localhost` and LAN hosts. So
there is nothing to add, and no per-origin permission prompt to grant.

Use **Test connection** beside the field: it separates "not running" from
"running but JSON is off" from "running but its upstream engines are blocked".

If SearXNG returns nothing or errors, the run falls back to Brave (when a key is
set) and then DuckDuckGo, and the panel shows a non-blocking notice naming the
provider that was used. The other providers are used exactly as selected; see
*Search providers* above.

## Signing in and reporting

Investigating needs no account. **Reporting does**, so a report can be attributed
to the person who filed it — and withdrawn by them later.

- The panel's report button opens the website's account page
  (`<website URL>/?ext=<extension id>#/profile`) in a tab when you are anonymous,
  where the "Sign in with Google" button waits. After sign-in that tab hands the
  session (account id, email, display name and a short-lived token) back to the
  extension over the `chrome.runtime` bridge — no copy-pasting.
- **My reports** in the panel (top right) lists everything you filed with its
  review status, and lets you **withdraw** one. It is the same data as the
  website's *My reports* page.
- **Sign out** deletes the stored session from the extension.
- The panel sends the token as `Authorization: Bearer …` with every report call;
  the backend verifies it before accepting anything. An expired token is caught
  and you are asked to sign in again.

Set `ADMIN_EMAILS` on the backend to open the admin dashboard, which reviews the
report queue and can accept, reject, or requeue a report.

## Permissions, and why

- `host_permissions: <all_urls>` — required to fetch search-result and result
  pages from arbitrary sites. Extension fetches with host permissions bypass
  CORS, which is what lets this work without a paid search API.
- `offscreen` — to host the crawler with DOM parsing and a long enough lifetime.
- `sidePanel` — the UI surface.
- `storage` — to remember your settings and last input locally.
- `externally_connectable` — not a permission but worth knowing: it lists the web
  origins allowed to connect (`http://localhost/*`, `https://*.vercel.app/*`).
  Without it, **no web page can reach the extension** and the website's inline
  check cannot work. Narrow the list once you have a custom domain.

## Privacy

Captured page text is sent to **your** backend for analysis. Nothing is sent to
Exa or any search API. Settings and the last input live in local extension
storage only. If you sign in so you can report a posting, the extension also
stores the session the website hands it (account id, email, display name, token)
until you sign out.

The full policy — what is read, what is sent to the backend, what is stored, and
what is never done — lives at `frontend/public/privacy.html` and is served on the
deployed site at `/privacy`. Submit that URL in the Chrome Web Store listing.

## Known limitations

- **Search engines can block automated fetches.** DuckDuckGo's HTML endpoints
  now routinely answer with an anti-bot page (HTTP 202) and should not be relied
  on; Brave (with a key) and your own SearXNG instance are the dependable
  options. A block or an unreachable provider is reported rather than failing
  silently.
- **SERPdive is currently unreachable.** Its SDK is real (npm `serpdive`, MIT,
  pinned here to the frozen `v1` contract), but as of **12 October 2026**
  `api.serpdive.com` returns `NXDOMAIN` from both Google and Cloudflare
  resolvers and `serpdive.com` itself has no `A` record, so a run on that
  provider fails with “Could not reach SERPdive”. The integration is written to
  the documented contract and will work if the service comes up, but it has
  **not** been verified against the live API. Nothing else depends on it: remove
  the `serpdive` option, its key field and `searchSerpdive` to drop it.
- **Scraping search results may violate a search engine's terms of service.**
  Fine for a personal tool; review before publishing.
- **Store review is the main risk.** Requesting `<all_urls>` and scraping
  arbitrary pages draws scrutiny, so expect to justify it in the Privacy tab (it
  reads the result pages a search returns) and allow for a slower review. Keep an
  unpacked/developer-mode install as a fallback.
- **Your browser must be open** for a run — there is no server-side crawler.
- Sites that render entirely via JavaScript may return little text from a plain
  `fetch` (this crawler does not execute page scripts).
