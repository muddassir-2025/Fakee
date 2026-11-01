"""End-to-end API tests for user-supplied Brave Search keys.

These assert the externally visible contract of the BYOK refactor:

* ``POST /api/investigate`` accepts a per-request key and runs a real (mocked)
  Brave search with it;
* a rejected key / exhausted quota is reported as such, and never as a low-risk
  verdict built on fabricated evidence;
* the key never appears in a response body;
* ``POST /api/search/validate`` tests a key without running an investigation.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.main import app
from app.services.brave import brave_client
from app.services.exa import SearchBundle, SearchResult
from app.services.search_errors import SearchAuthError, SearchQuotaError


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


SCAM_TEXT = (
    "Company: ABC Technologies\n"
    "They contacted me on WhatsApp and selected me without an interview.\n"
    "They asked for a Rs 1,500 registration fee. Website: abc-careers.xyz"
)
KEY = "BSA_test_key_9f8e7d6c5b4a"


@pytest.mark.asyncio
async def test_investigate_uses_the_user_key(client: httpx.AsyncClient, monkeypatch) -> None:
    calls: list[str] = []

    async def fake_search_many(groups, user_input, api_key, **kwargs):
        calls.append(api_key)
        return SearchBundle(
            results=[
                SearchResult(
                    query="q",
                    category="scam_complaints",
                    title="ABC Technologies scam",
                    url="https://forum.example/abc",
                    snippet="ABC Technologies asked for a registration fee over WhatsApp.",
                    source_domain="forum.example",
                )
            ],
            used_mock=False,
            queries_run=1,
        )

    monkeypatch.setattr(brave_client, "search_many", fake_search_many)

    response = await client.post(
        "/api/investigate",
        json={"text": SCAM_TEXT, "persist": False, "brave_api_key": KEY},
    )
    assert response.status_code == 200
    body = response.json()
    assert calls == [KEY]
    assert body["risk"]["level"] in {"HIGH", "CRITICAL"}
    # The key must never be echoed back in any part of the response.
    assert KEY not in json.dumps(body)


@pytest.mark.asyncio
async def test_invalid_key_is_reported_not_scored_low(client: httpx.AsyncClient, monkeypatch) -> None:
    async def fake_search_many(groups, user_input, api_key, **kwargs):
        raise SearchAuthError("The Brave Search API key was rejected.")

    monkeypatch.setattr(brave_client, "search_many", fake_search_many)

    response = await client.post(
        "/api/investigate",
        json={"text": SCAM_TEXT, "persist": False, "brave_api_key": KEY},
    )
    # A rejected key is an error, not a "LOW risk" result.
    assert response.status_code == 401
    body = response.json()
    assert body["detail"]["kind"] == "invalid_key"
    assert KEY not in json.dumps(body)


@pytest.mark.asyncio
async def test_exhausted_quota_is_429(client: httpx.AsyncClient, monkeypatch) -> None:
    async def fake_search_many(groups, user_input, api_key, **kwargs):
        raise SearchQuotaError("The Brave Search allowance for this key is exhausted.")

    monkeypatch.setattr(brave_client, "search_many", fake_search_many)

    response = await client.post(
        "/api/investigate",
        json={"text": SCAM_TEXT, "persist": False, "brave_api_key": KEY},
    )
    assert response.status_code == 429
    assert response.json()["detail"]["kind"] == "quota_exhausted"


@pytest.mark.asyncio
async def test_validate_endpoint_ok(client: httpx.AsyncClient, monkeypatch) -> None:
    class FakeQuota:
        limit = 15000
        remaining = 14999
        reset_seconds = 1000.0

    async def fake_validate(key: str):
        assert key == KEY
        return FakeQuota()

    monkeypatch.setattr(brave_client, "validate_key", fake_validate)
    response = await client.post("/api/search/validate", json={"provider": "brave", "api_key": KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["remaining"] == 14999
    assert KEY not in json.dumps(body)


@pytest.mark.asyncio
async def test_validate_endpoint_reports_bad_key(client: httpx.AsyncClient, monkeypatch) -> None:
    async def fake_validate(key: str):
        raise SearchAuthError("The Brave Search API key was rejected.")

    monkeypatch.setattr(brave_client, "validate_key", fake_validate)
    response = await client.post("/api/search/validate", json={"provider": "brave", "api_key": KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["kind"] == "invalid_key"
    assert KEY not in json.dumps(body)


@pytest.mark.asyncio
async def test_no_key_falls_back_to_demo(client: httpx.AsyncClient) -> None:
    """Without any key the labelled demo search still runs end to end."""
    response = await client.post("/api/investigate", json={"text": SCAM_TEXT, "persist": False})
    assert response.status_code == 200
