"""Tests for the browser-extension capture flow.

The extension searches and scrapes client-side, then posts pages back. These
tests cover stage-1 planning (/queries) and the evidence-based investigation.
"""

from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.schemas import ProvidedPage
from app.services.exa import results_from_pages

SCAM_TEXT = (
    "Company: ABC Technologies\n"
    "They contacted me on WhatsApp and selected me without an interview.\n"
    "They asked for a Rs 1,500 registration fee. Website: abc-careers.xyz"
)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


def test_results_from_pages_maps_and_dedupes() -> None:
    pages = [
        ProvidedPage(url="https://news.example.com/abc", title="Scam?", text="asked for a fee"),
        ProvidedPage(url="https://news.example.com/abc", title="dup", text="duplicate"),
        ProvidedPage(url="", title="no url", text="skip me"),
    ]
    results = results_from_pages(pages)
    assert len(results) == 1
    assert results[0].source_domain == "news.example.com"
    assert results[0].category == "browser_capture"
    assert results[0].is_mock is False


@pytest.mark.asyncio
async def test_queries_endpoint_returns_plan(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/queries", json={"text": SCAM_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["input"]["company"]["name"] == "ABC Technologies"
    assert body["input"]["money_request"]["detected"] is True
    assert body["queries"], "expected at least one query group"
    assert body["extraction_source"] in {"groq", "heuristic"}


@pytest.mark.asyncio
async def test_queries_endpoint_extracts_a_one_line_posting(client: httpx.AsyncClient) -> None:
    """A posting whose fields share one line must still name the company.

    The same posting on one line is the shape the README documents, so the
    stage-1 plan must be built from "ABC Technologies" and not from the whole
    sentence — the name keys the topic filter and the search queries, so a
    sentence leaking in misdirects the investigation.
    """
    one_line = (
        "Company: ABC Technologies. Selected on WhatsApp without interview. "
        "Pay Rs 1,500 registration fee. Website: abc-careers.xyz"
    )
    response = await client.post("/api/queries", json={"text": one_line})
    assert response.status_code == 200
    body = response.json()
    assert body["input"]["company"]["name"] == "ABC Technologies"
    assert body["input"]["money_request"]["detected"] is True
    # The queries are actually built from the name — the point of the fix.
    assert any(
        "ABC Technologies" in query
        for group in body["queries"]
        for query in group["queries"]
    )


@pytest.mark.asyncio
async def test_investigate_with_evidence_uses_provided_pages(client: httpx.AsyncClient) -> None:
    payload = {
        "text": SCAM_TEXT,
        "persist": False,
        "pages": [
            {
                "url": "https://forum.example.com/abc-tech",
                "title": "Is ABC Technologies a scam?",
                "text": (
                    "ABC Technologies asked applicants for a registration fee over "
                    "WhatsApp and selected people without an interview. Total scam."
                ),
                "query": '"ABC Technologies" scam',
                "category": "scam_complaints",
            }
        ],
    }
    response = await client.post("/api/investigate/with-evidence", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["risk"]["level"] in {"HIGH", "CRITICAL"}
    assert body["risk"]["score"] > 0

    search_stage = next(s for s in body["stages"] if s["stage"] == "web_search")
    assert "captured by the client" in search_stage["detail"]

    # The captured content must flow through as evidence, not mock data.
    assert body["investigation"]["reviews"]["total_mentions"] >= 1


@pytest.mark.asyncio
async def test_structured_input_skips_re_extraction(client: httpx.AsyncClient) -> None:
    plan = (await client.post("/api/queries", json={"text": SCAM_TEXT})).json()
    response = await client.post(
        "/api/investigate/with-evidence",
        json={
            "text": SCAM_TEXT,
            "persist": False,
            "structured_input": plan["input"],
            "pages": [],
        },
    )
    assert response.status_code == 200
    stages = {s["stage"]: s["status"] for s in response.json()["stages"]}
    assert stages["extraction"] == "skipped"


@pytest.mark.asyncio
async def test_search_does_not_create_a_company_record(client: httpx.AsyncClient) -> None:
    """A plain search must not persist anything; storage is opt-in (reports only)."""
    before = (await client.get("/api/stats")).json()
    payload = {
        "text": SCAM_TEXT,
        "pages": [
            {
                "url": "https://forum.example.com/abc-tech",
                "title": "Is ABC Technologies a scam?",
                "text": "ABC Technologies asked for a registration fee over WhatsApp.",
                "query": '"ABC Technologies" scam',
                "category": "scam_complaints",
            }
        ],
        # No "persist" key on purpose: the default must not store.
    }
    response = await client.post("/api/investigate/with-evidence", json=payload)
    assert response.status_code == 200
    after = (await client.get("/api/stats")).json()
    assert after["investigations"] == before["investigations"]
    assert after["companies"] == before["companies"]


@pytest.mark.asyncio
async def test_with_evidence_rejects_tiny_input(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/investigate/with-evidence", json={"text": "hi", "pages": []})
    assert response.status_code == 422
