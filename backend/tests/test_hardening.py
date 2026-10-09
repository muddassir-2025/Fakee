"""Tests for the production-hardening additions (middleware, config, clients)."""

from __future__ import annotations

import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.config import Settings
from app.main import app
from app.middleware import BodySizeLimitMiddleware
from app.api.ratelimit import RateLimiter
from app.services.exa import ExaClient
from app.services.groq_client import GroqClient


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.mark.asyncio
async def test_health_reports_database_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_ok"] is True


@pytest.mark.asyncio
async def test_readiness_and_liveness_probes(client: httpx.AsyncClient) -> None:
    ready = await client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database_ok"] is True

    live = await client.get("/api/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_security_headers_are_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health/live")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_request_id_is_generated_and_propagated(client: httpx.AsyncClient) -> None:
    generated = await client.get("/api/health/live")
    assert generated.headers.get("x-request-id")

    supplied = await client.get("/api/health/live", headers={"X-Request-ID": "abc123"})
    assert supplied.headers["x-request-id"] == "abc123"


def test_production_config_gating() -> None:
    prod = Settings(environment="production")
    assert prod.is_production
    assert prod.docs_enabled_effective is False
    assert prod.verbose_errors is False

    dev = Settings(environment="development")
    assert dev.is_production is False
    assert dev.docs_enabled_effective is True
    assert dev.verbose_errors is True


@pytest.mark.asyncio
async def test_body_size_limit_rejects_oversized_request() -> None:
    async def ok(_request):
        return PlainTextResponse("ok")

    small_app = Starlette(routes=[Route("/", ok, methods=["POST"])])
    small_app.add_middleware(BodySizeLimitMiddleware, max_bytes=10)

    transport = httpx.ASGITransport(app=small_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.post("/", content=b"x" * 100)).status_code == 413
        assert (await c.post("/", content=b"x" * 5)).status_code == 200


def test_rate_limiter_evicts_stale_keys() -> None:
    limiter = RateLimiter(limit=5, window_seconds=1, max_keys=100)
    limiter.check("client-a")
    limiter.check("client-b")
    assert len(limiter._hits) == 2

    time.sleep(1.1)
    limiter._evict_expired(time.monotonic())
    assert len(limiter._hits) == 0

    limiter.check("client-c")
    limiter.reset()
    assert len(limiter._hits) == 0


def test_rate_limiter_blocks_over_limit() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60)
    limiter.check("k")
    limiter.check("k")
    with pytest.raises(Exception) as exc:
        limiter.check("k")
    assert getattr(exc.value, "status_code", None) == 429


def test_clients_honour_retry_after_header() -> None:
    response = httpx.Response(429, headers={"retry-after": "2"})
    assert GroqClient._retry_delay(0, response) == 2.0
    assert ExaClient._retry_delay(0, response) == 2.0

    # Without the header, backoff grows with each attempt (plus jitter).
    first = GroqClient._retry_delay(0, None)
    later = GroqClient._retry_delay(3, None)
    assert later > first


@pytest.mark.asyncio
async def test_create_all_never_runs_against_postgres(monkeypatch) -> None:
    """PostgreSQL must be Alembic-owned.

    ``create_all`` on Postgres builds the tables without writing an
    ``alembic_version`` row, so the next ``alembic upgrade head`` collides with
    the initial migration (DuplicateTableError) and the container crash-loops.
    Only SQLite may auto-create.
    """
    import app.db as db

    created: list[bool] = []

    def spy(_conn) -> None:  # run_sync needs a *sync* callable
        created.append(True)

    monkeypatch.setattr(db.Base.metadata, "create_all", spy)

    # Postgres never creates, even when AUTO_CREATE_SCHEMA is left on.
    monkeypatch.setattr(db.settings, "database_url", "postgresql://u:p@host/db")
    monkeypatch.setattr(db.settings, "auto_create_schema", True)
    await db.init_db()
    assert created == []

    # SQLite (dev and the test suite) still auto-creates.
    monkeypatch.setattr(db.settings, "database_url", "sqlite+aiosqlite:///./data/x.db")
    await db.init_db()
    assert created == [True]

    # The explicit opt-out still defers to Alembic.
    created.clear()
    monkeypatch.setattr(db.settings, "auto_create_schema", False)
    await db.init_db()
    assert created == []
