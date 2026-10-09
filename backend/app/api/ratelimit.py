"""Minimal in-memory rate limiter.

Adequate for a single-instance deployment and for protecting the expensive
investigation endpoint. The store is bounded (keys are evicted once their
windows expire) so it cannot grow without limit under a spray of client IPs.

For multi-instance production, swap the store for Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from ..config import settings


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _evict_expired(self, now: float) -> None:
        """Drop keys whose buckets are empty/stale. Run when the store is full."""
        stale = [key for key, bucket in self._hits.items() if not bucket or now - bucket[-1] > self.window]
        for key in stale:
            self._hits.pop(key, None)

    def check(self, key: str) -> None:
        now = time.monotonic()
        if key not in self._hits and len(self._hits) >= self.max_keys:
            self._evict_expired(now)
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if not bucket:
            # Reclaim empty buckets so idle clients do not pin memory.
            self._hits.pop(key, None)
            bucket = self._hits[key]
        if len(bucket) >= self.limit:
            retry = int(self.window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )
        bucket.append(now)

    def reset(self) -> None:
        self._hits.clear()


def _client_key(request: Request) -> str:
    # X-Forwarded-For is spoofable, so only trust it when explicitly configured
    # to be behind a proxy/load balancer.
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


investigate_limiter = RateLimiter(
    limit=settings.rate_limit_investigations,
    window_seconds=settings.rate_limit_window_seconds,
    max_keys=settings.rate_limit_max_keys,
)
report_limiter = RateLimiter(
    limit=settings.rate_limit_reports,
    window_seconds=settings.rate_limit_window_seconds,
    max_keys=settings.rate_limit_max_keys,
)
query_limiter = RateLimiter(
    limit=settings.rate_limit_queries,
    window_seconds=settings.rate_limit_window_seconds,
    max_keys=settings.rate_limit_max_keys,
)


async def limit_investigations(request: Request) -> None:
    investigate_limiter.check(_client_key(request))


async def limit_reports(request: Request) -> None:
    report_limiter.check(_client_key(request))


async def limit_queries(request: Request) -> None:
    query_limiter.check(_client_key(request))
