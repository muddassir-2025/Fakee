"""Company-level response cache — a cost control.

Repeated investigations of the same opportunity (the same scam doing the rounds,
or a company a user checks twice) would otherwise re-run the whole pipeline and
re-spend Groq/Search budget. This cache serves the finished, company-level
result within a TTL.

What is stored: the risk assessment, evidence and structured company fields —
public/intelligence data. The user's raw report text is **stripped** before
storage, so caching a search never persists what the user typed (consistent with
the opt-in storage policy).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ResponseCache
from ..schemas import UserInput
from .text_utils import extract_domain, normalize_name

logger = logging.getLogger(__name__)

# Bump when the stored response shape or the scoring model changes materially,
# so stale entries from an older engine are not served.
CACHE_VERSION = "v1"


def _primary_domain(user_input: UserInput) -> str | None:
    if user_input.contacts.domains:
        return user_input.contacts.domains[0]
    return extract_domain(user_input.company.website)


def build_key(user_input: UserInput) -> str | None:
    """Deterministic cache key, or None when there is nothing to key on."""
    name = normalize_name(user_input.company.name)
    domain = _primary_domain(user_input) or ""
    if not name and not domain:
        return None
    return hashlib.sha256(f"{CACHE_VERSION}|{name}|{domain}".encode()).hexdigest()


def _sanitize(payload: dict) -> dict:
    """Drop the user's raw text before storing a cached response."""
    payload = dict(payload)
    user_input = payload.get("input")
    if isinstance(user_input, dict):
        user_input = dict(user_input)
        user_input["raw_text"] = ""
        payload["input"] = user_input
    return payload


async def get_cached(session: AsyncSession, cache_key: str) -> dict | None:
    """Return a live cached payload, or None when missing/expired."""
    row = (
        await session.execute(select(ResponseCache).where(ResponseCache.cache_key == cache_key))
    ).scalar_one_or_none()
    if row is None:
        return None

    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite returns naive datetimes
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        await session.delete(row)
        await session.commit()
        return None

    row.hits += 1
    await session.commit()
    return row.payload


async def store_cached(
    session: AsyncSession,
    cache_key: str,
    *,
    user_input: UserInput,
    response,
) -> None:
    """Insert or refresh a cached company-level response."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(60, settings.response_cache_ttl_seconds))
    payload = _sanitize(response.model_dump(mode="json"))

    existing = (
        await session.execute(select(ResponseCache).where(ResponseCache.cache_key == cache_key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.payload = payload
        existing.score = response.risk.score
        existing.level = response.risk.level
        existing.expires_at = expires
        existing.company_name = (user_input.company.name or None)
        existing.domain = _primary_domain(user_input)
    else:
        session.add(
            ResponseCache(
                cache_key=cache_key,
                company_name=(user_input.company.name or None),
                domain=_primary_domain(user_input),
                payload=payload,
                score=response.risk.score,
                level=response.risk.level,
                expires_at=expires,
            )
        )
    await session.commit()
