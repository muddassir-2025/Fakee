"""Persisted, deployment-wide budget readings for the rate-limited providers.

A run spends two free allowances, and only one of them is visible to this
process:

* **Groq** — the backend makes these calls, so its rate-limit headers arrive on
  every response and are captured in ``groq_client``.
* **Brave** — the search happens in the user's browser, so the backend never
  sees those headers; the extension reports what it read (``POST /budget/brave``).

Both end up in one table so the panel can show a single bar over whatever the
run actually costs. The two properties that matter:

**Global, not per user.** The allowances belong to the deployment's one free
account, so each provider window has exactly one reading that every visitor
shares. Nothing in this module is keyed by user, and nothing here is user data.

**In the database, not in process memory.** The free-tier host spins the
instance down; an in-memory snapshot came back empty on every restart, which is
why the bar used to read "Run an investigation to load the Groq budget" long
after a run had happened.

The Brave report is display-only. It is rate-limited but deliberately
unauthenticated (the extension runs anonymously), so the worst a forged report
achieves is a wrong bar until the next real search overwrites it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ProviderBudget

logger = logging.getLogger(__name__)

PROVIDER_GROQ = "groq"
PROVIDER_BRAVE = "brave"

# Window lengths in seconds, used only to decide when an un-refreshed reading is
# too old to still stand for "now". Brave's own monthly window is 2,592,000
# seconds (30 days), which is where that number comes from.
WINDOW_SECONDS: dict[str, int] = {"minute": 60, "day": 86_400, "month": 2_592_000}

# The one source of truth for what the bar is built from, in display order.
# `in_bar` separates what the bar draws from what is only worth a footnote: the
# Groq token window refills every minute, so drawing it would make the bar jump
# on every run for no useful signal.
SEGMENT_SPECS: tuple[dict, ...] = (
    {
        "key": "groq_requests",
        "provider": PROVIDER_GROQ,
        "label": "Groq requests",
        "period": "day",
        "in_bar": True,
    },
    {
        "key": "brave_searches",
        "provider": PROVIDER_BRAVE,
        "label": "Brave searches",
        "period": "month",
        "in_bar": True,
    },
    {
        "key": "groq_tokens",
        "provider": PROVIDER_GROQ,
        "label": "Groq tokens",
        "period": "minute",
        "in_bar": False,
    },
)
SEGMENT_ORDER: dict[str, int] = {spec["key"]: index for index, spec in enumerate(SEGMENT_SPECS)}
SEGMENTS_BY_KEY: dict[str, dict] = {spec["key"]: spec for spec in SEGMENT_SPECS}

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", re.IGNORECASE)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86_400.0}


def parse_duration_seconds(value: str | None) -> float | None:
    """Parse a provider's reset duration ("1m26.4s", "7.66s") into seconds.

    Groq reports its reset windows as compact durations rather than timestamps.
    Returns ``None`` when there is nothing parseable, so a caller falls back to
    "no known reset time" instead of inventing one.
    """
    if not value:
        return None
    total = 0.0
    found = False
    for amount, unit in _DURATION_PART.findall(value):
        total += float(amount) * _UNIT_SECONDS[unit.lower()]
        found = True
    return total if found else None


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC (as the cache does)."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _reset_at_from(now: datetime, reset_seconds: float | None) -> datetime | None:
    if reset_seconds is None or reset_seconds < 0:
        return None
    return now + timedelta(seconds=reset_seconds)


async def record_budget(
    session: AsyncSession,
    *,
    key: str,
    provider: str,
    period: str,
    limit: int | None = None,
    remaining: int | None = None,
    reset_seconds: float | None = None,
    source: str = "headers",
    now: datetime | None = None,
) -> ProviderBudget:
    """Upsert the latest reading for one provider window.

    Only the fields a caller actually observed are written: a response that
    carries a remaining count but no limit must not erase the limit already
    known from an earlier call.
    """
    now = now or datetime.now(timezone.utc)

    row = await session.get(ProviderBudget, key)
    if row is None:
        row = ProviderBudget(budget_key=key, provider=provider, period=period)
        session.add(row)

    row.provider = provider
    row.period = period
    if limit is not None:
        row.limit_value = limit
    if remaining is not None:
        row.remaining = remaining
    reset_at = _reset_at_from(now, reset_seconds)
    if reset_at is not None:
        row.reset_at = reset_at
    row.source = source
    row.updated_at = now

    await session.flush()
    return row


async def record_groq_snapshot(
    session: AsyncSession, snapshot: dict, *, now: datetime | None = None
) -> None:
    """Mirror a captured Groq quota snapshot into the shared store."""
    if not snapshot:
        return
    now = now or datetime.now(timezone.utc)
    pairs = (
        (SEGMENTS_BY_KEY["groq_requests"], "limit_requests", "remaining_requests", "reset_requests"),
        (SEGMENTS_BY_KEY["groq_tokens"], "limit_tokens", "remaining_tokens", "reset_tokens"),
    )
    for spec, limit_key, remaining_key, reset_key in pairs:
        limit = snapshot.get(limit_key)
        remaining = snapshot.get(remaining_key)
        if limit is None and remaining is None:
            continue
        await record_budget(
            session,
            key=spec["key"],
            provider=spec["provider"],
            period=spec["period"],
            limit=limit,
            remaining=remaining,
            reset_seconds=parse_duration_seconds(snapshot.get(reset_key)),
            source="groq_headers",
            now=now,
        )


async def read_budget_rows(session: AsyncSession) -> list[ProviderBudget]:
    """Every stored reading (one row per provider window, shared by all)."""
    result = await session.execute(select(ProviderBudget))
    return list(result.scalars().all())


def _segment(
    spec: dict,
    *,
    limit: int | None,
    remaining: int | None,
    reset_at: datetime | None,
    updated_at: datetime | None,
    source: str = "headers",
    now: datetime | None = None,
) -> dict:
    """Normalize one reading into the shape the panel renders."""
    now = now or datetime.now(timezone.utc)
    reset_at = _aware(reset_at)
    updated_at = _aware(updated_at)
    reset_assumed = False

    # The provider's own clock beats a guess: once the reset time it reported has
    # passed, the allowance is full again even though nothing has measured it.
    if reset_at is not None and reset_at <= now and limit is not None:
        remaining = limit
        reset_assumed = True

    stale = False
    if remaining is None or limit is None:
        stale = True
    elif not reset_assumed and reset_at is None and updated_at is not None:
        # Nothing measured it recently and there is no reset time to justify
        # carrying the number forward, so report it as unknown rather than wrong.
        if (now - updated_at).total_seconds() > WINDOW_SECONDS.get(spec["period"], 86_400):
            remaining = None
            stale = True

    percent: float | None = None
    if limit and remaining is not None:
        percent = round(min(100.0, max(0.0, remaining / limit * 100)), 1)

    return {
        "key": spec["key"],
        "provider": spec["provider"],
        "label": spec["label"],
        "period": spec["period"],
        "in_bar": spec["in_bar"],
        "limit": limit,
        "remaining": remaining,
        "percent_remaining": percent,
        "reset_at": reset_at,
        "reset_assumed": reset_assumed,
        "stale": stale,
        "updated_at": updated_at,
        "source": source,
    }


def build_segments(rows: Iterable[ProviderBudget], *, now: datetime | None = None) -> list[dict]:
    """Normalize stored rows into segments, in display order."""
    segments: list[dict] = []
    for row in rows:
        spec = SEGMENTS_BY_KEY.get(row.budget_key)
        if spec is None:  # a reading for a provider that is no longer tracked
            continue
        segments.append(
            _segment(
                spec,
                limit=row.limit_value,
                remaining=row.remaining,
                reset_at=row.reset_at,
                updated_at=row.updated_at,
                source=row.source,
                now=now,
            )
        )
    return sort_segments(segments)


def sort_segments(segments: list[dict]) -> list[dict]:
    return sorted(segments, key=lambda s: SEGMENT_ORDER.get(s["key"], len(SEGMENT_SPECS)))


def groq_segments_from_snapshot(
    snapshot: dict, *, now: datetime | None = None
) -> list[dict]:
    """Segments built from the in-process Groq snapshot.

    The database row is the real source of truth; this covers the window where a
    reading has just been captured but not yet written (or a write that failed),
    so the bar is never blank while the numbers are still in hand.
    """
    if not snapshot or (
        snapshot.get("limit_requests") is None and snapshot.get("limit_tokens") is None
    ):
        return []

    now = now or datetime.now(timezone.utc)
    updated_at = None
    raw_updated = snapshot.get("updated_at")
    if isinstance(raw_updated, str):
        try:
            updated_at = datetime.fromisoformat(raw_updated)
        except ValueError:
            updated_at = None

    built = []
    pairs = (
        (SEGMENTS_BY_KEY["groq_requests"], "limit_requests", "remaining_requests", "reset_requests"),
        (SEGMENTS_BY_KEY["groq_tokens"], "limit_tokens", "remaining_tokens", "reset_tokens"),
    )
    for spec, limit_key, remaining_key, reset_key in pairs:
        built.append(
            _segment(
                spec,
                limit=snapshot.get(limit_key),
                remaining=snapshot.get(remaining_key),
                reset_at=_reset_at_from(now, parse_duration_seconds(snapshot.get(reset_key))),
                updated_at=updated_at,
                source="groq_headers",
                now=now,
            )
        )
    return sort_segments(built)


def filter_in_play(segments: list[dict], providers: set[str] | None) -> list[dict]:
    """Keep the budgets a run can actually spend.

    Groq is always in play (every investigation costs requests). The rest are
    kept only when the caller says it has that search provider in play, so a
    DuckDuckGo run does not show a Brave allowance it will never touch.
    """
    if providers is None:
        return segments
    return [s for s in segments if s["provider"] == PROVIDER_GROQ or s["provider"] in providers]
