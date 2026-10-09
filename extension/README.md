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
   │                            │  search web (DuckDuckGo/Brave)    │
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

## Configuration

Open **Settings** in the side panel:

| Setting | Default | Notes |
| --- | --- | --- |
| Backend API base | `http://localhost:8000/api` | Where your FastAPI backend runs |
| Search provider | DuckDuckGo (free, no key) | DuckDuckGo's HTML endpoint needs no API key |
| Brave API key | — | Optional; enables the Brave Search free tier |

The backend must allow the extension origin. It does by default via
`CORS_ORIGIN_REGEX=chrome-extension://.*`.

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
storage only.

The full policy — what is read, what is sent to the backend, what is stored, and
what is never done — lives at `frontend/public/privacy.html` and is served on the
deployed site at `/privacy`. Submit that URL in the Chrome Web Store listing.

## Known limitations

- **Search engines can rate-limit or CAPTCHA** automated fetches, especially
  Google. DuckDuckGo's HTML endpoint is the most tolerant; Brave (with a key) is
  the most reliable.
- **Scraping search results may violate a search engine's terms of service.**
  Fine for a personal tool; review before publishing.
- **Store review is the main risk.** Requesting `<all_urls>` and scraping
  arbitrary pages draws scrutiny, so expect to justify it in the Privacy tab (it
  reads the result pages a search returns) and allow for a slower review. Keep an
  unpacked/developer-mode install as a fallback.
- **Your browser must be open** for a run — there is no server-side crawler.
- Sites that render entirely via JavaScript may return little text from a plain
  `fetch` (this crawler does not execute page scripts).
