"""Test configuration: keep the suite hermetic and on a local SQLite database.

The app also reads DATABASE_URL from the project-root ``.env`` (for Neon in
production). Tests must not touch that remote database, so we pin a local
SQLite URL *before* any app module — and therefore ``Settings`` — is imported.
Environment variables take precedence over dotenv values in pydantic-settings.
"""

from __future__ import annotations

import os

# Pin every external dependency so the suite is hermetic, deterministic and
# network-free (env vars take precedence over the project's .env).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_investigations.db"
os.environ["GROQ_API_KEY"] = ""
os.environ["EXA_API_KEY"] = ""
# Auth defaults to *unconfigured*: the fail-closed tests (503, not 401) assert
# that, and a developer's local .env pointing at a real Neon Auth project must
# not change what the suite verifies. The signed-in/report/admin tests opt in
# explicitly through the `configured_auth` fixture.
os.environ["NEON_AUTH_BASE_URL"] = ""
os.environ["NEON_AUTH_JWKS_URL"] = ""
os.environ["NEON_AUTH_ISSUER"] = ""
# The company-level response cache persists across requests; keep it off so one
# test cannot be served another test's result. Cache tests enable it explicitly.
os.environ["RESPONSE_CACHE_ENABLED"] = "false"
