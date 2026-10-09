"""API tests. These use the app in-process; external services fall back to mocks."""

from __future__ import annotations

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert {"groq", "exa", "database"} <= set(body["integrations"])


@pytest.mark.asyncio
async def test_investigate_rejects_tiny_input(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/investigate", json={"text": "hi", "persist": False})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_investigate_returns_full_contract(client: httpx.AsyncClient) -> None:
    payload = {
        "text": (
            "Company: ABC Technologies\n"
            "They contacted me on WhatsApp and said I was selected without an interview.\n"
            "They asked for a Rs 1,500 registration fee. Website: abc-careers.xyz"
        ),
        "persist": False,
    }
    response = await client.post("/api/investigate", json=payload)
    assert response.status_code == 200
    body = response.json()

    assert set(body) >= {"id", "input", "investigation", "risk", "stages"}
    assert 0 <= body["risk"]["score"] <= 100
    assert body["risk"]["level"] in {"LOW", "MODERATE", "HIGH", "CRITICAL"}
    assert body["input"]["company"]["name"] == "ABC Technologies"
    assert body["input"]["money_request"]["detected"] is True
    # The evidence signals the scoring depends on must survive serialization, so
    # a client can see *why* a posting was rated the way it was.
    reviews = body["investigation"]["reviews"]
    assert {
        "negative_mentions",
        "fraud_accusations",
        "link_verified_by_official_source",
        "claim_contradicted",
        "negative_source_domains",
    } <= set(reviews)
    for signal in body["risk"]["signals"]:
        assert signal["id"] and signal["explanation"]


def test_storage_is_opt_in_by_default() -> None:
    """Searches must not be stored; only an explicit report persists a record."""
    from app.schemas import EvidenceInvestigationRequest, InvestigationRequest

    assert EvidenceInvestigationRequest(text="abc").persist is False
    assert InvestigationRequest(text="abc").persist is False


@pytest.mark.asyncio
async def test_groq_quota_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/groq/quota")
    assert response.status_code == 200
    body = response.json()
    assert "enabled" in body


def test_groq_quota_captures_rate_limit_headers() -> None:
    """The client should surface Groq's rate-limit headers as a quota snapshot."""
    import httpx as _httpx

    from app.services.groq_client import GroqClient

    client = GroqClient()
    response = _httpx.Response(
        200,
        headers={
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "997",
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "7530",
            "x-ratelimit-reset-requests": "1m26.4s",
        },
    )
    client._record_quota(response)
    quota = client.quota()
    assert quota["limit_requests"] == 1000
    assert quota["remaining_requests"] == 997
    assert quota["limit_tokens"] == 8000
    assert quota["remaining_tokens"] == 7530


@pytest.mark.asyncio
async def test_submit_scam_report_requires_sign_in(client: httpx.AsyncClient) -> None:
    """Reporting is a write about a named business, so it needs a signed-in user.

    This suite runs with no auth project configured, so the endpoint fails closed
    (503). The authenticated path — identity recorded, ownership, admin review —
    is covered in ``test_reports_auth.py``.
    """
    response = await client.post(
        "/api/reports",
        json={
            "text": (
                "Company: ABC Technologies\n"
                "Asked for a Rs 1,500 registration fee over WhatsApp."
            ),
            "report_type": "scam",
            "source": "extension_user",
        },
    )
    assert response.status_code == 503
    assert (await client.get("/api/reports/mine")).status_code == 503


@pytest.mark.asyncio
async def test_patterns_catalog(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/patterns")
    assert response.status_code == 200
    patterns = {p["pattern"] for p in response.json()}
    assert "upfront_payment" in patterns
    assert "recent_domain" in patterns
    assert "sensitive_data_request" in patterns


@pytest.mark.asyncio
async def test_unknown_investigation_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/investigations/nope")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_metrics_endpoint_reports_requests_and_investigations(
    client: httpx.AsyncClient,
) -> None:
    from app.services import metrics

    metrics.reset()
    await client.post(
        "/api/investigate",
        json={"text": "Company: ABC Technologies asked for a fee.", "persist": False},
    )

    body = (await client.get("/api/metrics")).json()
    assert "counters" in body and "histograms" in body
    assert body["counters"]["investigations"]["outcome=success|stage=api"] == 1
    # Request counters are keyed by route template, not by opaque ids.
    assert any("route=/api/investigate" in key for key in body["counters"]["http_requests"])

    text = (await client.get("/api/metrics/prometheus")).text
    assert "http_request_duration_seconds_bucket" in text
    assert "process_uptime_seconds" in text


@pytest.mark.asyncio
async def test_metrics_labels_do_not_leak_ids(client: httpx.AsyncClient) -> None:
    from app.services import metrics

    metrics.reset()
    await client.get("/api/investigations/abc123def456")
    body = (await client.get("/api/metrics")).json()
    routes = [key.split("|")[1] for key in body["counters"].get("http_requests", {})]
    assert routes  # the 404 request was recorded
    # The route is reported as its template, never with the raw id segment.
    assert all("{" in r for r in routes)
    assert not any("abc123def456" in r for r in routes)
