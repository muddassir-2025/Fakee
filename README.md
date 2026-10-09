# Fakee

An **evidence-based** detector for fake jobs and internships. Instead of a black-box
"fake or real" classifier, it runs a full investigation:

> User input → structured data → web investigation → evidence extraction → scam-pattern
> detection → database correlation → risk assessment → **explainable result**

The differentiator is that **multiple independent evidence sources are combined into an
explainable investigation**, and the final verdict is computed by a deterministic,
reproducible rule engine — not decided arbitrarily by an LLM.

---

## Architecture

```
                         USER
                           │
              Paste Job / Internship Info
                           │
                           ↓
                    ┌─────────────┐
                    │    React    │  frontend/
                    │  Frontend   │
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │   FastAPI   │  backend/app/api
                    │   Backend   │
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │   Groq AI   │  services/extraction.py
                    │  Extract →  │  (JSON 1: what the user knows)
                    │    JSON     │
                    └──────┬──────┘
                           ↓
                 Structured User Data
                           │
             ┌─────────────┼──────────────┐
             ↓             ↓              ↓
          Exa          Domain Check     Database
       Search/Content   RDAP/DNS/HTTPS   History      services/{exa,domain_check,repository}.py
             │             │              │
             └─────────────┼──────────────┘
                           ↓
                    Investigation Data
                           ↓
                    ┌─────────────┐
                    │   Groq AI   │  services/evidence.py
                    │  Structure  │  (JSON 2: what the investigation discovered)
                    │  Evidence   │
                    └──────┬──────┘
                           ↓
                  Pattern Engine      services/patterns.py   ← deterministic rules
                           ↓
                    Risk Engine       services/risk.py       ← weighted, explainable score
                           ↓
                    Database (memory) services/repository.py
                           ↓
                    React Result UI
```

### How the plan maps to the code

| Plan component            | Implementation                                                             |
| ------------------------- | -------------------------------------------------------------------------- |
| React website             | `frontend/` — landing page (React + Tailwind); explains the tool and routes users to the extension |
| Python backend            | `backend/app/` — FastAPI + async orchestration                             |
| Groq — extraction (JSON 1) | `services/extraction.py`                                                  |
| Groq — evidence (JSON 2)  | `services/evidence.py`                                                     |
| Exa search/content        | `services/exa.py` (free tier)                                              |
| Categorized query builder | `services/query_builder.py`                                                |
| Domain verification       | `services/domain_check.py` (RDAP + DNS + HTTPS + redirect + name match)    |
| Scam-pattern engine       | `services/patterns.py` (29 patterns across money/channel/PII/opportunity/domain/reputation/history) |
| Risk engine               | `services/risk.py` (severity weights + co-occurrence amplifiers)           |
| Intelligence database     | `models.py` (companies, opportunities, domains, investigations, patterns, evidence, risk, reports) |
| Orchestration             | `services/pipeline.py`                                                     |

---

## Quick start

Requires **Python 3.11+** and **Node 18+**.

### 1. Backend

Easiest — the start script finds the venv itself and works on Windows (Git Bash),
macOS and Linux:

```bash
./scripts/start-backend.sh              # http://127.0.0.1:8000
RELOAD=1 ./scripts/start-backend.sh     # auto-reload while editing
PORT=9000 ./scripts/start-backend.sh    # custom port
```

Or with `make` (if installed): `make backend`, `make dev`, `make test`.

Manual equivalent:

```bash
cd backend
python -m venv .venv
# Windows (Git Bash):
./.venv/Scripts/python.exe -m pip install -r requirements.txt
# macOS / Linux:
source .venv/bin/activate && pip install -r requirements.txt

cp .env.example .env          # optional — see "Configuration"
uvicorn app.main:app --reload --port 8000
```

API docs: <http://127.0.0.1:8000/docs>

### 2. Website

The `frontend/` app is the **landing site** (React + Tailwind). It explains the
problem, shows how the tool works, and routes people to the browser extension —
the actual investigation runs client-side, not on this site.

Once the extension is installed, the site's **Check a posting** panel runs a real
investigation inline: the page talks to the extension over a `chrome.runtime`
Port, the extension searches and reads pages in the user's browser, and the
verdict is rendered on the page.

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Set the extension's store URL in `frontend/src/content.ts` (`EXTENSION.storeUrl`)
when the listing is published — the extension id is read from that URL, which is
how the page can reach the extension — or override it per deploy with
`VITE_EXTENSION_STORE_URL`. Until then the site shows the manual install steps.

### Run with Docker

```bash
docker compose up --build     # frontend on :8080, API on :8000
```

---

## Browser extension (primary client)

The `extension/` folder is a Manifest V3 Chrome extension that does the **search
and page reading in your browser**, so no paid search API is needed. It asks the
backend to plan queries, runs the searches itself, scrapes the result pages, and
posts the captured text back for analysis.

See [`extension/README.md`](extension/README.md) for how it works, install steps
and limitations. The React web app still works for server-side runs, but the
extension is the primary client.

```
Side panel → POST /api/queries → search + scrape in the browser
           → POST /api/investigate/with-evidence (captured pages) → verdict
```

The backend enables this with two endpoints (above) and allows the extension
origin via `CORS_ORIGIN_REGEX` (default `chrome-extension://.*`).

The website can drive the same crawl: it connects to the extension over a Port
(see [The website ⇄ extension bridge](#the-website--extension-bridge)) so a
visitor can paste a posting on the site and read the verdict there, with the
search still running in their own browser.

Useful targets:

```bash
make extension-zip EXT_ARGS="--api-base https://<your-backend>/api"  # store package
make check-bridge                                                   # protocol parity
```

## Configuration

Everything is optional. **With no keys the app runs fully in demo mode** — Groq falls
back to deterministic heuristics and Exa falls back to clearly-labelled mock
search results, so the whole pipeline still runs end-to-end.

Copy `backend/.env.example` to `backend/.env` (or to the repo root as `.env` — both
are read, with `backend/.env` taking precedence) and set what you have:

| Variable              | Purpose                                          | Without it                         |
| --------------------- | ------------------------------------------------ | ---------------------------------- |
| `DATABASE_URL`        | Neon PostgreSQL (plain `postgresql://…` is auto-normalized to asyncpg + TLS) | local SQLite is used automatically |
| `GROQ_API_KEY`        | LLM extraction (JSON 1) + evidence structuring (JSON 2) | heuristic extraction is used |
| `GROQ_MODEL`          | Groq model id                                     | `openai/gpt-oss-120b`              |
| `CORS_ORIGINS`        | Comma-separated allowed frontend origins          | `http://localhost:5173`            |

**Exa is optional and off by default.** The Chrome extension performs the
searches and page reads in the user's own browser, so the product runs end to
end with no search API. Setting `EXA_ENABLED=true` plus `EXA_API_KEY` only
enables the server-side `POST /api/investigate` path (otherwise that endpoint
returns deterministic mock results). The supporting code is kept for that case;
nothing else depends on it.

> The `/api/health` endpoint reports which integrations are live and exposes a
> `mock_mode` flag the UI surfaces as a banner.

---

## API

| Method | Path                        | Description                          |
| ------ | --------------------------- | ------------------------------------ |
| `GET`  | `/api/health`               | Status + which integrations are live |
| `GET`  | `/api/health/live`          | Liveness probe (process only)        |
| `GET`  | `/api/health/ready`         | Readiness probe (+ real DB check)    |
| `POST` | `/api/investigate`          | Run a full investigation (server-side search) |
| `POST` | `/api/queries`              | Stage-1 query plan for the extension |
| `POST` | `/api/investigate/with-evidence` | Investigate pages captured by the client |
| `POST` | `/api/analyst`             | On-demand analyst read over captured pages |
| `GET`  | `/api/investigations`       | Recent investigations                |
| `GET`  | `/api/investigations/{id}`  | Reload a stored investigation        |
| `POST` | `/api/reports`              | Submit a user report                 |
| `GET`  | `/api/stats`                | Aggregate stats                      |
| `GET`  | `/api/metrics`              | Operational metrics (JSON)           |
| `GET`  | `/api/metrics/prometheus`   | Same metrics, Prometheus text format |
| `GET`  | `/api/patterns`             | The scam-pattern catalog             |

```bash
curl -s http://127.0.0.1:8000/api/investigate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Company: ABC Technologies. Selected on WhatsApp without interview. Pay Rs 1,500 registration fee. Website: abc-careers.xyz"}'
```

### Response shape

```jsonc
{
  "id": "…",
  "input":         { /* JSON 1 — what the user knows */ },
  "investigation": { /* JSON 2 — what the investigation discovered */ },
  "risk": {
    "score": 92, "level": "CRITICAL", "confidence": 0.5,
    "headline": "Very likely a fake / fraudulent opportunity",
    "signals": [ { "id": "upfront_payment", "severity": "critical", "points": 68.4, "explanation": "…" } ],
    "verified":   ["Website domain appears recently registered.", "…"],
    "unverified": ["Whether the offer is officially authorised by the named organisation."],
    "source_urls": ["…"]
  },
  "stages": [ { "stage": "extraction", "status": "ok", "duration_ms": 41 } ]
}
```

---

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Two additional runnable scripts:

```bash
python -m scripts.smoke_test   # end-to-end pipeline over sample scam/legit inputs
python -m scripts.http_test    # boots the real server in-process and exercises the API
```

Frontend typecheck/build:

```bash
cd frontend && npm run typecheck && npm run build
```

---

## Production notes

Everything below is on by default and needs no extra setup.

- **Resilience.** Groq and Exa calls use exponential backoff with jitter, honour
  `Retry-After`, and retry on 429/5xx; Exa queries run with bounded concurrency
  (`EXA_MAX_CONCURRENCY`). If a provider still fails, the pipeline degrades
  gracefully to heuristics / mock search instead of erroring out.
- **Security.** Every response carries defensive headers (CSP, `X-Frame-Options`,
  `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`) and HSTS in
  production. Request bodies over `MAX_REQUEST_BYTES` are rejected with `413`.
  Set `TRUSTED_HOSTS` and `ENVIRONMENT=production` to lock down the Host header
  and disable interactive docs.
- **Observability.** Every request gets an `X-Request-ID` (generated or
  propagated) that appears in structured access logs and in error responses, so
  failures are traceable end to end.
- **Health.** `/api/health/ready` performs a real database round-trip and returns
  `503` when the database is unreachable — wire it to your load balancer / k8s
  readiness probe. `/api/health/live` is a dependency-free liveness check.
- **Rate limiting.** `/api/investigate` and `/api/reports` are rate-limited per
  client IP (`RATE_LIMIT_*`); the in-memory store is bounded. Set
  `TRUST_PROXY=true` only when running behind a trusted proxy (the Docker setup
  does).
- **Database pooling.** PostgreSQL uses a sized, recycled connection pool
  (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS`) with
  `pool_pre_ping`.
- **Containers.** Both images run as non-root with healthchecks, and
  `docker compose up --build` wires the frontend to wait for a healthy backend.
- **Migrations.** The schema is versioned with Alembic. The container entrypoint
  runs `alembic upgrade head` before serving, and `AUTO_CREATE_SCHEMA=false`
  there makes Alembic (not `create_all`) the single source of schema truth.
  Locally, `make migrate` (or `cd backend && python -m alembic upgrade head`)
  applies migrations; `alembic check` fails if the models drift from them.
- **Metrics.** `/api/metrics` (JSON) and `/api/metrics/prometheus` (text) expose
  request counts by route template + status, latency histograms, and
  investigation/query/report counters — process-local, no agent required.
- **Cost controls.** A company-level response cache (`RESPONSE_CACHE_ENABLED`,
  `RESPONSE_CACHE_TTL_SECONDS`) serves a repeated check without re-running
  search + Groq, and caches only public intelligence (the user's raw text is
  stripped). The analyst read is bundled into the evidence call — one request,
  no extra cost — but `include_analyst=false` skips it, and `POST /api/analyst`
  fetches it on demand.
- **Accuracy gate.** `python -m scripts.evaluate` measures precision/recall/F1
  over the labelled seed set and exits non-zero below the bar.
- **CI.** `.github/workflows/ci.yml` runs the backend test suite, checks that
  migrations apply cleanly and match the models, runs the accuracy gate, and
  builds + typechecks the frontend on every push and PR.
- **Errors.** Unhandled exceptions return a JSON body with a request id; internal
  exception text is only exposed when `ENVIRONMENT` is not `production`.

## Deploying

Three pieces to ship, and only the first is a plain static host:

| Piece | Host | Why |
| --- | --- | --- |
| Website (`frontend/`) | **Vercel** | Static Vite build: root directory `frontend`, build `npm run build`, output `dist`. |
| Backend (`backend/`) | **Render** (or any container host) | The extension calls it, so it must be a public HTTPS URL. |
| Extension (`extension/`) | **Chrome Web Store** | One-time $5 developer registration, then review. |

The website does **not** call the backend — the extension does. A `localhost`
backend therefore works only on your own machine, so deploy it before publishing.

### Backend on Render

Build from the existing `backend/Dockerfile` (the container entrypoint runs
`alembic upgrade head` before serving). Set:

- `GROQ_API_KEY`
- `DATABASE_URL` — your Neon connection string (plain `postgresql://` is fine; it is normalized)
- `CORS_ORIGINS` — include your Vercel origin, e.g. `https://<your-app>.vercel.app`
- `ENVIRONMENT=production` — disables interactive docs and internal error details

The **container entrypoint runs `alembic upgrade head` before serving**, and on
PostgreSQL the schema is owned by Alembic alone. `AUTO_CREATE_SCHEMA` is ignored
there (it only applies to SQLite), because `create_all` against Postgres builds
tables with no `alembic_version` row — a schema the migrations do not know about.

#### If the deploy crash-loops with `relation "companies" already exists`

That means the database was previously populated outside the entrypoint (for
example, running `scripts/start-backend.sh` locally with a Neon `DATABASE_URL` in
your `.env`). The tables exist but Alembic has no record of them. Adopt the
existing schema, then confirm it really matches the models:

```bash
cd backend
export DATABASE_URL="postgresql://..."   # your Neon URL
python -m alembic stamp head           # the tables already match head
python -m alembic check                # must print "No new upgrade operations detected."
```

If `alembic check` reports drift, the database is not at head — and if there is
no data to keep, reset it instead and let Alembic build it:

```bash
psql "$DATABASE_URL" -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
```

Then redeploy.

### Extension package

```bash
make extension-zip EXT_ARGS="--api-base https://<your-backend>/api"
# -> dist/fakee-extension-<version>.zip
```

Only runtime files are included, `manifest.json` sits at the zip root, and the
deployed API base is baked in as the default (it warns you if the build still
points at localhost). Upload that zip in the
[Chrome Web Store developer dashboard](https://chrome.google.com/webstore/devconsole).
The listing needs screenshots, a privacy-policy URL, and a justification for
`<all_urls>` (it has to read the result pages a search returns). The policy page
is committed at `frontend/public/privacy.html` and is served on the deployed site
at `/privacy` — use that URL in the listing. It states what the extension reads,
what is sent to the backend, what is stored (and that a plain search stores
nothing), and what is never done.

Once approved, the store URL ends with the extension id. Put that URL in
`EXTENSION.storeUrl` (or set `VITE_EXTENSION_STORE_URL`) so the website can detect
the extension and run investigations inline.

### The website ⇄ extension bridge

Chrome only lets a page connect to an extension that lists the page's origin in
its manifest `externally_connectable` (currently `http://localhost/*` and
`https://*.vercel.app/*` — narrow this once you have a custom domain). The page
must also know the extension id, which is why it is read from the store URL.

```
page  -> connect(extensionId, "fakee-bridge")
page  -> { type: "PING" }              extension -> { type: "PONG" }
page  -> { type: "RUN", text }         extension -> PROGRESS… -> RESULT | ERROR
```

The service worker keeps the Port alive for the duration of a run, relays the
offscreen crawler's progress and result to the page, and re-checks the sender
origin. `make check-bridge` asserts both halves still agree on the Port name,
message types and allowed origins.

### Scaling

For multiple backend instances, run uvicorn with workers
(`uvicorn app.main:app --workers 4`) and swap the in-memory rate-limit store for
Redis (see `backend/app/api/ratelimit.py`).

## Design notes

- **Signals, not keywords.** Detection looks for *combinations* of signals. A low-cost
  TLD alone is a weak `low` signal; an upfront fee **plus** a WhatsApp-only process
  **plus** a recently registered domain is critical. `services/risk.py` applies explicit
  co-occurrence amplifiers rather than adding independent scores.
- **Context vs. evidence.** Messaging-app contact (WhatsApp/Telegram) and urgency are
  common in *legitimate* Indian hiring, so they are weighted `low` and can never, on
  their own, produce a HIGH/CRITICAL verdict — a hard signal (money, sensitive-data
  request, impersonation/domain evidence, or corroborated reports) is required.
- **Trust is attribution, not adjacency.** A positive signal is only awarded for a
  domain *attributable* to the named employer — its own label (`adp.com`), a subdomain
  of it (`jobs.adp.com`), or the brand plus a short generic suffix (`eteaminc.com` for
  "eTeam"). A link the posting merely happens to contain is never credited as "the
  employer's own domain": a shortener, a Google Form or a hosting subdomain is not the
  employer's property, and claiming otherwise is exactly what a scam relies on. A
  registration link that is *not* the employer's domain is scored instead
  (`shortener_link`, `unofficial_application_channel`) — and because genuine campus
  drives do use Google Forms and college shorteners, neither can alone produce a HIGH
  verdict.
- **The score follows the kind of evidence, not its volume.** Dissatisfaction and
  fraud are counted separately: `negative_reputation` covers complaints about an
  employer (process, pay, reviews) and tops out inside MODERATE, because a company can
  be a famously poor employer and still run a genuine drive. Only
  `fraud_accusations_against_company` — independent sources alleging the company
  *itself* defrauded people — or evidence the posting's terms are contradicted by an
  official source (`claim_contradicted_by_official_source`) may reach HIGH. Accusations
  are subtracted from the dissatisfaction count so one body of reports is not scored
  twice. Impersonation reports are excluded throughout: there the company is the victim.
- **Official sources can clear a channel as well as contradict a claim.** If an
  authoritative page (a government portal, an official college notice) reproduces the
  posting's own registration link, the "unofficial channel" signal stands down and trust
  records it (`official_link_verified`) — that is what a legitimate campus programme
  using a Google Form looks like. If an official source instead states *different*
  terms, that contradiction is scored.
- **Two documents, not one.** JSON 1 is what the user reported; JSON 2 is what the
  investigation discovered. Keeping them separate keeps the output auditable.
- **Deterministic verdict.** The LLM extracts and summarizes; the score, level and
  signals come from the rule engine, so results are reproducible and explainable.
- **Honest mock mode.** Demo data is labelled, deterministic, and never presented as real
  evidence (`mock.freebuff.dev`), and the API reports `mock_mode: true`.
- **Privacy by default.** Searching does not store anything. A company record is only
  written when the user explicitly reports a posting (the "Report as scam" button →
  `POST /api/reports`), and those reports then correlate over time.

## Disclaimer

Signals are advisory. Always verify an employer through a channel you initiate yourself,
and never pay for a job, internship, or "training".
