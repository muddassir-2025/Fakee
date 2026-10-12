"""Budget bar tests: the persisted, deployment-wide Groq + Brave readings.

The bar's whole point is that it is *shared* and *durable*, so these assert both:
readings survive a brand-new database session (not just process memory), no
segment is keyed by user, and only the budgets a caller has in play are counted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete

from app.db import SessionLocal, engine
from app.main import app
from app.models import Base, ProviderBudget
from app.services.budget import (
    SEGMENTS_BY_KEY,
    build_segments,
    filter_in_play,
    groq_segments_from_snapshot,
    parse_duration_seconds,
    read_budget_rows,
    record_budget,
    record_groq_snapshot,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture(autouse=True)
async def clean_budgets():
    """The store is global, so each test must start from an empty table.

    The schema is created here rather than relying on the app lifespan: several
    tests exercise the store directly, and this fixture would otherwise run
    before the app ever builds its tables.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await session.execute(delete(ProviderBudget))
        await session.commit()
    yield


# ------------------------------------------------------------------ parsing ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1m26.4s", 86.4),
        ("7.66s", 7.66),
        ("2h3m", 7380.0),
        ("500ms", 0.5),
        ("2d", 172_800.0),
        ("", None),
        (None, None),
        ("soon", None),
    ],
)
def test_parse_duration_seconds(value: str | None, expected: float | None) -> None:
    """Groq reports reset windows as compact durations, not timestamps."""
    assert parse_duration_seconds(value) == expected


# --------------------------------------------------------------- durability --
@pytest.mark.asyncio
async def test_reading_survives_a_new_database_session() -> None:
    """Stored server-side, so a restart (or a reconnecting client) keeps it.

    This uses two independent sessions deliberately: an in-memory snapshot would
    pass a same-session assertion, which is exactly the bug being fixed.
    """
    async with SessionLocal() as writer:
        await record_budget(
            writer,
            key="groq_requests",
            provider="groq",
            period="day",
            limit=1000,
            remaining=999,
            source="groq_headers",
            now=NOW,
        )
        await writer.commit()

    async with SessionLocal() as reader:
        rows = await read_budget_rows(reader)

    segments = {s["key"]: s for s in build_segments(rows, now=NOW)}
    assert segments["groq_requests"]["remaining"] == 999
    assert segments["groq_requests"]["limit"] == 1000
    assert segments["groq_requests"]["percent_remaining"] == 99.9


@pytest.mark.asyncio
async def test_upsert_keeps_the_previous_limit_when_a_reading_omits_it() -> None:
    async with SessionLocal() as session:
        await record_budget(
            session, key="brave_searches", provider="brave", period="month", limit=1000, now=NOW
        )
        await record_budget(
            session, key="brave_searches", provider="brave", period="month", remaining=987, now=NOW
        )
        await session.commit()

    async with SessionLocal() as session:
        rows = await read_budget_rows(session)

    segment = build_segments(rows, now=NOW)[0]
    assert (segment["limit"], segment["remaining"]) == (1000, 987)


# ------------------------------------------------------- window arithmetic ---
@pytest.mark.asyncio
async def test_elapsed_reset_time_means_the_allowance_is_full_again() -> None:
    """A window the provider said had reset is reported as refilled, and flagged."""
    async with SessionLocal() as session:
        await record_budget(
            session,
            key="groq_requests",
            provider="groq",
            period="day",
            limit=1000,
            remaining=10,
            reset_seconds=60,
            now=NOW - timedelta(hours=1),
        )
        await session.commit()

    async with SessionLocal() as session:
        rows = await read_budget_rows(session)

    segment = build_segments(rows, now=NOW)[0]
    assert segment["remaining"] == 1000
    assert segment["reset_assumed"] is True
    assert segment["percent_remaining"] == 100.0


@pytest.mark.asyncio
async def test_aged_reading_without_a_reset_time_is_reported_unknown() -> None:
    """Rather than carry a day-old number forward as if it were current."""
    async with SessionLocal() as session:
        await record_budget(
            session,
            key="groq_tokens",
            provider="groq",
            period="minute",
            limit=8000,
            remaining=6754,
            now=NOW - timedelta(hours=3),
        )
        await session.commit()

    async with SessionLocal() as session:
        rows = await read_budget_rows(session)

    segment = build_segments(rows, now=NOW)[0]
    assert segment["remaining"] is None
    assert segment["stale"] is True
    assert segment["percent_remaining"] is None


@pytest.mark.asyncio
async def test_recent_reading_is_not_stale() -> None:
    async with SessionLocal() as session:
        await record_budget(
            session,
            key="groq_tokens",
            provider="groq",
            period="minute",
            limit=8000,
            remaining=6754,
            now=NOW - timedelta(seconds=20),
        )
        await session.commit()

    async with SessionLocal() as session:
        segment = build_segments(await read_budget_rows(session), now=NOW)[0]

    assert segment["stale"] is False
    assert segment["remaining"] == 6754


# ------------------------------------------------------------- Groq capture --
@pytest.mark.asyncio
async def test_groq_snapshot_writes_both_windows_with_parsed_resets() -> None:
    """The requests (day) and tokens (minute) windows are captured separately."""
    snapshot = {
        "limit_requests": 1000,
        "remaining_requests": 997,
        "limit_tokens": 8000,
        "remaining_tokens": 7530,
        "reset_requests": "1m26.4s",
        "reset_tokens": "7.66s",
    }
    async with SessionLocal() as session:
        await record_groq_snapshot(session, snapshot, now=NOW)
        await session.commit()

    async with SessionLocal() as session:
        segments = {s["key"]: s for s in build_segments(await read_budget_rows(session), now=NOW)}

    assert segments["groq_requests"]["remaining"] == 997
    assert segments["groq_requests"]["reset_at"] == NOW + timedelta(seconds=86.4)
    assert segments["groq_tokens"]["remaining"] == 7530
    assert segments["groq_tokens"]["reset_at"] == NOW + timedelta(seconds=7.66)
    # The per-minute token window is a footnote, not a bar segment.
    assert segments["groq_requests"]["in_bar"] is True
    assert segments["groq_tokens"]["in_bar"] is False


def test_in_process_snapshot_can_stand_in_for_a_missing_row() -> None:
    """Before the first write lands, the bar still has numbers to show."""
    snapshot = {
        "limit_requests": 1000,
        "remaining_requests": 999,
        "limit_tokens": 8000,
        "remaining_tokens": 6754,
        "reset_requests": "10m",
        "updated_at": NOW.isoformat(),
    }
    segments = {s["key"]: s for s in groq_segments_from_snapshot(snapshot, now=NOW)}
    assert segments["groq_requests"]["remaining"] == 999
    assert groq_segments_from_snapshot({}, now=NOW) == []


# ------------------------------------------------------------- in-play rule --
def test_only_budgets_in_play_are_counted() -> None:
    """A DuckDuckGo run must not show a Brave allowance it will never spend."""
    segments = [
        {"key": "groq_requests", "provider": "groq"},
        {"key": "brave_searches", "provider": "brave"},
    ]
    assert [s["key"] for s in filter_in_play(segments, {"duckduckgo"})] == ["groq_requests"]
    assert [s["key"] for s in filter_in_play(segments, {"brave"})] == [
        "groq_requests",
        "brave_searches",
    ]
    assert [s["key"] for s in filter_in_play(segments, None)] == [
        "groq_requests",
        "brave_searches",
    ]


# ---------------------------------------------------------------- endpoints --
@pytest.mark.asyncio
async def test_budget_endpoint_returns_groq_then_brave_when_in_play(
    client: httpx.AsyncClient,
) -> None:
    # A Groq reading, as an earlier run would have left behind.
    async with SessionLocal() as session:
        await record_groq_snapshot(
            session,
            {
                "limit_requests": 1000,
                "remaining_requests": 999,
                "limit_tokens": 8000,
                "remaining_tokens": 6754,
                "reset_requests": "10m",
            },
        )
        await session.commit()

    report = await client.post(
        "/api/budget/brave",
        json={"limit": 15000, "remaining": 14523, "reset_seconds": 1_234_567},
    )
    assert report.status_code == 200, report.text
    assert report.json()["ok"] is True

    response = await client.get("/api/budget", params={"in_play": "brave"})
    assert response.status_code == 200
    body = response.json()
    assert "enabled" in body
    keys = [segment["key"] for segment in body["segments"]]
    assert keys == ["groq_requests", "brave_searches", "groq_tokens"]

    groq = next(s for s in body["segments"] if s["key"] == "groq_requests")
    assert (groq["limit"], groq["remaining"]) == (1000, 999)

    brave = next(s for s in body["segments"] if s["key"] == "brave_searches")
    assert brave["remaining"] == 14523
    assert brave["limit"] == 15000
    assert brave["source"] == "brave_headers"
    assert brave["percent_remaining"] == 96.8


@pytest.mark.asyncio
async def test_budget_endpoint_omits_brave_when_it_is_not_in_play(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/api/budget/brave", json={"limit": 15000, "remaining": 14523})

    response = await client.get("/api/budget", params={"in_play": "duckduckgo"})
    keys = [segment["key"] for segment in response.json()["segments"]]
    assert "brave_searches" not in keys


@pytest.mark.asyncio
async def test_brave_report_is_shared_by_every_caller(client: httpx.AsyncClient) -> None:
    """One free account, so the reading is global — never per client or user.

    Two separate requests (each with its own database session, and neither
    signed in) must see the same numbers, and nothing user-scoped may be stored.
    """
    await client.post("/api/budget/brave", json={"limit": 15000, "remaining": 14000})

    first = await client.get("/api/budget", params={"in_play": "brave"})
    second = await client.get("/api/budget", params={"in_play": "brave"})
    assert first.json() == second.json()

    columns = set(ProviderBudget.__table__.columns.keys())
    assert not {c for c in columns if "user" in c or "session" in c or "client" in c}


@pytest.mark.asyncio
async def test_brave_report_rejects_impossible_numbers(client: httpx.AsyncClient) -> None:
    too_many = await client.post("/api/budget/brave", json={"limit": 10, "remaining": 50})
    assert too_many.status_code == 422

    negative = await client.post("/api/budget/brave", json={"limit": 10, "remaining": -3})
    assert negative.status_code == 422

    empty = await client.post("/api/budget/brave", json={})
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_brave_report_records_the_monthly_window_of_a_multi_window_plan(
    client: httpx.AsyncClient,
) -> None:
    """Brave reports per-second and per-month windows together; the bar uses the month.

    The extension picks the largest window out of `X-RateLimit-Policy` and sends
    only that pair, which is what this pins down at the API boundary.
    """
    specs = SEGMENTS_BY_KEY["brave_searches"]
    assert specs["period"] == "month"
    assert specs["in_bar"] is True

    await client.post("/api/budget/brave", json={"limit": 15000, "remaining": 14999})
    response = await client.get("/api/budget", params={"in_play": "brave,tinyfish"})
    brave = next(s for s in response.json()["segments"] if s["key"] == "brave_searches")
    assert brave["remaining"] == 14999


@pytest.mark.asyncio
async def test_signed_out_caller_sees_the_bar(client: httpx.AsyncClient) -> None:
    """Investigating is anonymous, so the bar must not require a session."""
    response = await client.get("/api/budget")
    assert response.status_code == 200
    assert isinstance(response.json()["segments"], list)
