"""Cost-control tests: company-level response cache + on-demand analyst."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete

from app.config import settings as app_settings
from app.db import SessionLocal
from app.main import app
from app.models import ResponseCache
from app.schemas import (
    CompanyInfo,
    EvidenceInvestigationRequest,
    Investigation,
    InvestigationResponse,
    InvestigationRequest,
    RiskAssessment,
    UserInput,
)
from app.services.cache import build_key, get_cached, store_cached


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


def _user_input(name: str = "Cache Reuse Ltd") -> UserInput:
    return UserInput(company=CompanyInfo(name=name, website="cache-reuse.example"))


def _response(user_input: UserInput, score: int = 55) -> InvestigationResponse:
    return InvestigationResponse(
        id="cached123",
        created_at=datetime.now(timezone.utc),
        status="completed",
        input=user_input,
        investigation=Investigation(company=user_input.company),
        risk=RiskAssessment(
            score=score,
            level="MODERATE",
            headline="Possibly risky",
            summary="s",
            recommendation="r",
            confidence=0.5,
        ),
    )


def test_cache_key_is_stable_and_requires_identity() -> None:
    a = _user_input()
    assert build_key(a) == build_key(_user_input())
    # A different company must not collide.
    assert build_key(a) != build_key(_user_input("Other Co"))
    # Nothing to key on -> no key (and therefore no caching).
    assert build_key(UserInput()) is None


@pytest.mark.asyncio
async def test_cache_round_trip_strips_raw_text_and_counts_hits() -> None:
    user_input = _user_input()
    user_input = user_input.model_copy(update={"raw_text": "secret user text"})
    key = build_key(user_input)
    assert key

    async with SessionLocal() as session:
        try:
            await store_cached(session, key, user_input=user_input, response=_response(user_input))
            cached = await get_cached(session, key)
            assert cached is not None
            assert cached["risk"]["score"] == 55
            # The user's raw text must never be stored.
            assert cached["input"]["raw_text"] == ""

            first = await get_cached(session, key)
            assert first is not None  # hit increments
            row = (
                await session.execute(select_row(key))
            ).scalar_one()
            assert row.hits == 2
        finally:
            await session.execute(delete(ResponseCache).where(ResponseCache.cache_key == key))
            await session.commit()


@pytest.mark.asyncio
async def test_expired_cache_entry_is_removed() -> None:
    key = build_key(_user_input("Expired Co")) or "expired"
    async with SessionLocal() as session:
        session.add(
            ResponseCache(
                cache_key=key,
                company_name="Expired Co",
                domain="cache-reuse.example",
                payload={"risk": {"score": 1}},
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
            )
        )
        await session.commit()
        assert await get_cached(session, key) is None
        remaining = (
            await session.execute(select_row(key))
        ).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
async def test_cached_investigation_is_served_without_repipeline(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cache is off for the rest of the suite; enable it for this test only.
    monkeypatch.setattr(app_settings, "response_cache_enabled", True)
    user_input = _user_input("Cache Serve Ltd")
    key = build_key(user_input)
    assert key
    async with SessionLocal() as session:
        await store_cached(
            session, key, user_input=user_input, response=_response(user_input, score=72)
        )
    try:
        response = await client.post(
            "/api/investigate/with-evidence",
            json={
                "text": "Company: Cache Serve Ltd. Please review this posting.",
                "pages": [],
                "persist": False,
                "structured_input": user_input.model_dump(mode="json"),
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cached"] is True
        assert body["risk"]["score"] == 72
        assert body["stages"][0]["stage"] == "cache"
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(ResponseCache).where(ResponseCache.cache_key == key))
            await session.commit()


def test_include_analyst_flag_defaults_true() -> None:
    assert InvestigationRequest(text="abc").include_analyst is True
    assert EvidenceInvestigationRequest(text="abc").include_analyst is True
    assert EvidenceInvestigationRequest(text="abc", include_analyst=False).include_analyst is False


@pytest.mark.asyncio
async def test_on_demand_analyst_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/analyst",
        json={
            "text": "Company: ABC Technologies asked for a Rs 1,500 registration fee.",
            "pages": [
                {
                    "url": "https://example.com/report",
                    "title": "ABC Technologies scam report",
                    "text": "Several candidates said ABC Technologies asked for a registration fee.",
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert {"analyst", "reviews", "evidence", "notable_findings"} <= set(body)


def select_row(key: str):
    from sqlalchemy import select

    return select(ResponseCache).where(ResponseCache.cache_key == key)
