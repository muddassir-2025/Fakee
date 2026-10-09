"""Application configuration.

All external services are optional. When a key is absent the corresponding
service falls back to a deterministic mock so the app always runs end-to-end.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Read the project-root .env and the backend/.env (the latter wins) so the
        # app works whether the file lives at the repo root or beside the backend.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "Fakee"
    environment: str = "development"
    api_prefix: str = "/api"
    log_level: str = "INFO"

    # Comma-separated list of allowed frontend origins.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Regex for additional allowed origins (defaults to any browser extension).
    cors_origin_regex: str = "chrome-extension://.*"
    # Comma-separated allowed Host headers ("*" disables the check).
    trusted_hosts: str = "*"
    # Trust X-Forwarded-For/-Proto. Enable only when behind a known proxy/LB.
    trust_proxy: bool = False
    # Reject request bodies larger than this (bytes).
    max_request_bytes: int = 1_000_000
    # Expose interactive docs. Auto-disabled in production unless forced.
    docs_enabled: bool = True
    # Include internal error messages in 500 responses (never in production).
    expose_error_details: bool = True
    # Upper bound on a single investigation request (seconds).
    investigation_timeout_seconds: float = 240.0
    # CORS credentials require explicit origins; disabled when origins is "*".
    cors_allow_credentials: bool = True

    # --- Authentication (Neon Auth, a.k.a. Managed Better Auth) ---
    # The auth base URL from the Neon console, e.g.
    #   https://ep-xxx.neonauth.us-east-2.aws.neon.build/neondb/auth
    # Leave empty to run without sign-in (reporting is then refused, see
    # ``auth_configured``). The frontend's VITE_NEON_AUTH_URL must point at the
    # same value.
    neon_auth_base_url: str | None = None
    # Optional explicit JWKS URL. Defaults to <base>/.well-known/jwks.json.
    neon_auth_jwks_url: str | None = None
    # Optional explicit expected issuer. Defaults to the origin of the auth URL
    # (Managed Better Auth sets `iss` to the auth origin) plus the full base URL.
    neon_auth_issuer: str | None = None
    # How long fetched signing keys are cached before a re-fetch (seconds).
    neon_auth_jwks_cache_seconds: int = 3600
    # Emails allowed into the admin dashboard (comma-separated).
    admin_emails: str = "studymuddassir@gmail.com"
    # Hold new reports for review, or count them immediately.
    #   false (default): a report counts towards a company's history as soon as
    #     it is filed; the admin can still reject it, which removes it.
    #   true: nothing counts until the admin accepts it.
    reports_require_approval: bool = False

    # --- Database (Neon PostgreSQL in production, SQLite locally) ---
    # e.g. postgresql+asyncpg://user:pass@host/db?ssl=require
    database_url: str = "sqlite+aiosqlite:///./data/investigations.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    # Create tables from the models on startup. Fine for local dev/SQLite and
    # the test suite. Set false in production and manage the schema with
    # Alembic (`alembic upgrade head`), which the container entrypoint runs.
    auto_create_schema: bool = True

    # --- Groq (LLM extraction + evidence structuring) ---
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 45.0
    groq_max_retries: int = 3
    groq_backoff_base_seconds: float = 1.5
    # Max characters of captured-page evidence sent to Groq in one evidence
    # call. Kept modest so a single investigation fits Groq's free-tier budget
    # (openai/gpt-oss-120b: 8K tokens/minute). ~8K chars ≈ 2K tokens, which
    # leaves room for the system prompt, the user report and the JSON output.
    # Pages are ranked by relevance and reduced to their signal sentences before
    # this cap is applied, so it bounds tokens rather than which evidence
    # survives. Raise this if you are on a paid tier with a higher TPM.
    groq_evidence_max_chars: int = 8000

    # --- Exa (optional server-side web search) ---
    # Exa is NOT used by default. The browser extension performs the search and
    # page reads itself, so the product works with no search API at all. Set
    # EXA_ENABLED=true (and supply a key) only to enable the server-side
    # /investigate path. The code is kept for that case; nothing depends on it.
    exa_enabled: bool = False
    exa_api_key: str | None = None
    exa_base_url: str = "https://api.exa.ai"
    exa_search_type: str = "auto"
    exa_timeout_seconds: float = 45.0
    exa_max_retries: int = 3
    exa_backoff_base_seconds: float = 1.0
    # Parallel in-flight Exa queries (keeps us within rate limits).
    exa_max_concurrency: int = 5

    # --- Rate limiting (per client IP) ---
    rate_limit_investigations: int = 20
    rate_limit_reports: int = 60
    # Query planning is cheaper than a full investigation (or a browser capture
    # flow makes two calls), so it gets a more generous budget.
    rate_limit_queries: int = 120
    rate_limit_window_seconds: int = 300
    rate_limit_max_keys: int = 10_000

    # --- Cost controls ---
    # Cache investigation results by company + domain for a TTL so repeated
    # checks (the same scam doing the rounds) don't re-run the pipeline and
    # re-spend Groq/Search budget. Stores company-level intelligence only.
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = 86400
    # Skip the Groq evidence call when no captured page is actually about the
    # company (e.g. only generic scam-advice pages came back). There is nothing
    # company-specific to structure, so the result is reported as inconclusive
    # instead of paying for an empty structuring.
    skip_groq_when_evidence_irrelevant: bool = True
    # Skip the Groq extraction call when the deterministic extractor already
    # found both a subject (company/domain) and a concrete signal, and only ask
    # Groq for the messy/ambiguous cases it actually improves.
    prefer_heuristic_extraction: bool = True

    # --- Investigation tuning ---
    # Small, curated query budget: enough to cover the scam and reputation
    # themes without flooding the browser with dozens of searches per request.
    max_search_queries: int = 12
    max_scrape_pages: int = 12
    search_results_per_query: int = 6
    domain_recent_days: int = 180

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def docs_enabled_effective(self) -> bool:
        """Interactive docs default off in production unless explicitly enabled."""
        return self.docs_enabled and not self.is_production

    @property
    def verbose_errors(self) -> bool:
        """Never leak internal exception text in production."""
        return self.expose_error_details and not self.is_production

    @property
    def allow_cors_credentials(self) -> bool:
        # Credentials cannot be combined with a wildcard origin.
        return self.cors_allow_credentials and "*" not in self.cors_origin_list

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def exa_live(self) -> bool:
        """True only when server-side Exa search is both switched on and keyed."""
        return self.exa_enabled and bool(self.exa_api_key)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    # --- Auth helpers -----------------------------------------------------

    @property
    def auth_configured(self) -> bool:
        """True when a Neon Auth project has been wired up."""
        return bool((self.neon_auth_base_url or "").strip() or (self.neon_auth_jwks_url or "").strip())

    @property
    def jwks_url(self) -> str:
        """Where the public signing keys are published."""
        if self.neon_auth_jwks_url:
            return self.neon_auth_jwks_url.strip()
        return f"{(self.neon_auth_base_url or '').strip().rstrip('/')}/.well-known/jwks.json"

    @property
    def accepted_issuers(self) -> list[str]:
        """Token issuers we accept.

        Managed Better Auth sets ``iss`` to the *origin* of the auth URL, but the
        documented examples also show the full base URL, so both are accepted.
        ``NEON_AUTH_ISSUER`` overrides the whole list when set.
        """
        if self.neon_auth_issuer:
            return [self.neon_auth_issuer.strip()]
        base = (self.neon_auth_base_url or self.neon_auth_jwks_url or "").strip()
        if not base:
            return []
        base = base.rstrip("/")
        if base.endswith("/.well-known/jwks.json"):
            base = base[: -len("/.well-known/jwks.json")]
        parts = urlsplit(base)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
        issuers = [base]
        if origin and origin not in issuers:
            issuers.append(origin)
        return issuers

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        """Database URL normalized for SQLAlchemy's async engine.

        Accepts a plain ``postgresql://`` (or ``postgres://``) URL — as copied
        straight from the Neon dashboard — and rewrites it to the asyncpg
        dialect. libpq-only parameters that asyncpg rejects are dropped, and
        ``sslmode=require`` is translated to asyncpg's ``ssl=require``. Managed
        Postgres such as Neon requires TLS, so it is added when missing.
        """
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix):]
                break
        if not url.startswith("postgresql+asyncpg://"):
            return url

        parts = urlsplit(url)
        query: list[tuple[str, str]] = []
        has_ssl = False
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "channel_binding":  # libpq-only; asyncpg rejects it
                continue
            if key in {"sslmode", "ssl"}:
                key, has_ssl = "ssl", True
            query.append((key, value))
        if not has_ssl and "neon.tech" in parts.netloc:
            query.append(("ssl", "require"))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
