"""Brave Search integration with a **per-request, user-supplied** API key.

Design constraints (see the security requirements):

* The key is passed in per call and held only in the local ``headers`` of the
  request. There is no module-level key, no shared authenticated client, and no
  cache keyed by anything, so two concurrent investigations with two different
  keys cannot see each other's credential or results.
* The key is never logged, never returned, never attached to an exception, and
  never written anywhere. ``_redact`` scrubs it from any provider/transport error
  text as a last line of defence.
* A failure raises a typed :class:`~app.services.search_errors.SearchProviderError`
  so the caller can distinguish "the search did not run" from "the search ran and
  found nothing" — a failed search must never be scored as low risk.

Brave Web Search API: ``GET https://api.search.brave.com/res/v1/web/search``
with the key in the ``X-Subscription-Token`` header.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..schemas import SearchQueryGroup, UserInput
from .exa import SearchBundle, SearchResult
from .safe_fetch import safe_fetch
from .search_errors import (
    SearchAuthError,
    SearchConfigError,
    SearchNetworkError,
    SearchProviderError,
    SearchQuotaError,
    SearchRateLimitError,
)
from .text_utils import extract_domain

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class BraveQuota:
    """The monthly allowance Brave reports in its own rate-limit headers."""

    limit: int | None = None
    remaining: int | None = None
    reset_seconds: float | None = None
    readings: list[dict] = field(default_factory=list)


def _redact(text: str, api_key: str) -> str:
    """Remove the API key from any string before it can reach a log or a user."""
    if api_key and api_key in text:
        text = text.replace(api_key, "[redacted]")
    return text


def parse_quota_headers(headers: httpx.Headers) -> BraveQuota | None:
    """Read the monthly allowance out of Brave's comma-separated headers.

    Brave reports several windows at once (a per-second burst plus the monthly
    quota). The pair with the largest ``w=`` is the monthly window, which is the
    number that says how much of the free allowance is left.
    """

    def numbers(name: str) -> list[float]:
        raw = headers.get(name)
        if not raw:
            return []
        out: list[float] = []
        for part in raw.split(","):
            try:
                out.append(float(part.strip()))
            except ValueError:
                out.append(float("nan"))
        return out

    limits = numbers("x-ratelimit-limit")
    remaining = numbers("x-ratelimit-remaining")
    if not limits or not remaining:
        return None

    windows = [
        float((re.search(r"w=(\d+)", part) or [None, "nan"])[1])
        if part.strip()
        else float("nan")
        for part in (headers.get("x-ratelimit-policy") or "").split(",")
    ]
    usable = [w for w in windows if w == w]  # drop NaN
    if usable:
        index = windows.index(max(usable))
        if windows[index] < 3600:  # only a burst window exposed: nothing to report
            return None
    else:
        index = len(limits) - 1  # Brave lists the burst window first
    if index >= len(limits) or index >= len(remaining):
        return None

    resets = numbers("x-ratelimit-reset")
    reset = resets[index] if index < len(resets) and resets[index] == resets[index] else None
    return BraveQuota(
        limit=int(limits[index]),
        remaining=int(remaining[index]),
        reset_seconds=reset,
    )


def _classify(status: int, message: str, *, api_key: str) -> SearchProviderError:
    """Turn an HTTP status into the right typed error, key-free."""
    safe = _redact(message, api_key)
    if status in {401, 403}:
        return SearchAuthError(
            "The Brave Search API key was rejected. Check that it is correct and "
            "has the Web Search plan enabled.",
            detail=safe,
        )
    if status == 402:
        return SearchQuotaError(
            "The Brave Search allowance for this key is exhausted. Check your plan "
            "and billing in the Brave dashboard.",
            detail=safe,
        )
    if status == 429:
        return SearchRateLimitError(
            "Brave Search rate limit reached. Wait a moment and try again.",
            detail=safe,
        )
    return SearchProviderError(
        f"Brave Search request failed (HTTP {status}).", detail=safe
    )


class BraveClient:
    """Stateless Brave client. The key is a per-call argument, never stored."""

    def __init__(self) -> None:
        # A single HTTP client, deliberately **without** an Authorization header:
        # the key is attached per request so no credential is held in shared
        # state and concurrent users cannot observe each other's key.
        self._client: httpx.AsyncClient | None = None
        # The most recent quota reading, for the budget bar. It is provider-level
        # (not key- or user-level) and carries no credential.
        self.last_quota: BraveQuota | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.brave_base_url,
                timeout=settings.brave_timeout_seconds,
                # Redirects are not followed: the endpoint never redirects, and
                # following one could leak the key header to another host.
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                try:
                    return max(0.0, min(30.0, float(header)))
                except ValueError:
                    pass
        base = settings.brave_backoff_base_seconds
        return min(20.0, base * (2**attempt)) + random.uniform(0, 0.5)

    async def _get_with_retry(
        self, path: str, params: dict, api_key: str
    ) -> httpx.Response:
        """GET one Brave query, retrying transient failures and 429s."""
        client = await self._get_client()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }
        attempts = max(1, settings.brave_max_retries + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                # Redact before the message can escape into a traceback.
                last_exc = exc
                if attempt == attempts - 1:
                    raise SearchNetworkError(
                        "Could not reach Brave Search. Check the connection and try again.",
                        detail=_redact(str(exc), api_key),
                    ) from None
                await asyncio.sleep(self._retry_delay(attempt, None))
                continue
            quota = parse_quota_headers(response.headers)
            if quota is not None:
                self.last_quota = quota
            if response.status_code == 429 and attempt < attempts - 1:
                await asyncio.sleep(self._retry_delay(attempt, response))
                continue
            if response.status_code in RETRYABLE_STATUS and response.status_code != 429:
                if attempt < attempts - 1:
                    await asyncio.sleep(self._retry_delay(attempt, response))
                    continue
            return response
        raise SearchNetworkError(  # pragma: no cover - loop always returns or raises
            "Brave Search request failed.", detail=_redact(str(last_exc), api_key)
        )

    async def _search_one(self, query: str, api_key: str) -> list[SearchResult]:
        response = await self._get_with_retry(
            "/res/v1/web/search",
            {"q": query, "count": settings.brave_results_per_query},
            api_key,
        )
        if response.status_code >= 400:
            # Never echo the request body/headers; only the status and a short,
            # redacted reason, so a key can never appear in the message.
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("error") or payload.get("detail") or "")
            except Exception:  # noqa: BLE001
                detail = response.text[:200] if response.text else ""
            raise _classify(response.status_code, detail, api_key=api_key)

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                "Brave Search returned an unreadable response.",
                detail=_redact(str(exc), api_key),
            ) from None

        results: list[SearchResult] = []
        for item in (data.get("web", {}) or {}).get("results") or []:
            url = item.get("url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    query=query,
                    category="",
                    title=(item.get("title") or "")[:300],
                    url=url,
                    snippet=(item.get("description") or "").strip()[:600],
                    source_domain=extract_domain(url),
                )
            )
            if len(results) >= settings.brave_results_per_query:
                break
        return results

    async def search_many(
        self,
        groups: list[SearchQueryGroup],
        user_input: UserInput,
        api_key: str,
        *,
        fetch_pages: bool | None = None,
    ) -> SearchBundle:
        """Run a query plan through Brave using ``api_key`` for this call only.

        Raises :class:`SearchProviderError` if *every* query failed, so a total
        provider outage is reported rather than scored as "no evidence". If at
        least one query succeeded the bundle is returned (possibly empty); a
        partial failure is logged but does not sink a run that produced results.
        """
        key = (api_key or "").strip()
        if not key:
            raise SearchConfigError(
                "No Brave Search API key was provided for this investigation."
            )

        semaphore = asyncio.Semaphore(max(1, settings.brave_max_concurrency))

        async def run(category: str, query: str):
            async with semaphore:
                try:
                    return category, query, await self._search_one(query, key), None
                except SearchProviderError as exc:
                    return category, query, None, exc

        jobs = [(g.category, q) for g in groups for q in g.queries]
        outcomes = await asyncio.gather(*(run(c, q) for c, q in jobs))

        bundle = SearchBundle(used_mock=False)
        errors: list[SearchProviderError] = []
        for category, _query, results, error in outcomes:
            if error is not None:
                errors.append(error)
                continue
            if results:
                bundle.queries_run += 1
                for result in results:
                    result.category = category
                bundle.results.extend(results)

        if not bundle.results and errors:
            # Every query failed: surface the most informative error rather than
            # pretending the company has no footprint.
            errors.sort(key=lambda e: 0 if isinstance(e, SearchAuthError) else 1)
            raise errors[0]
        if not bundle.results and not errors and jobs:
            # The plan had queries but Brave returned nothing for any of them.
            # That is a real, empty search — kept empty so the verdict can be
            # "insufficient evidence" rather than a fabricated success.
            bundle.used_mock = False

        # Deduplicate by URL, keeping the first (highest-priority) category.
        seen: set[str] = set()
        deduped: list[SearchResult] = []
        for result in bundle.results:
            if result.url in seen:
                continue
            seen.add(result.url)
            deduped.append(result)
        bundle.results = deduped

        should_fetch = settings.brave_fetch_pages if fetch_pages is None else fetch_pages
        if should_fetch and bundle.results:
            await self._enrich_with_pages(bundle.results)

        return bundle

    async def _enrich_with_pages(self, results: list[SearchResult]) -> None:
        """Best-effort page text for the top results, via the SSRF-safe fetcher.

        Only the first ``brave_fetch_max_pages`` results are fetched, and each is
        capped in bytes. A page that cannot be read leaves its snippet in place.
        """
        targets = results[: max(0, settings.brave_fetch_max_pages)]
        outcomes = await asyncio.gather(
            *(safe_fetch(r.url, timeout_seconds=settings.brave_fetch_timeout_seconds) for r in targets)
        )
        for result, page in zip(targets, outcomes):
            if page is None or not page.text:
                continue
            # Keep the snippet's signal sentences and append the page body.
            combined = f"{result.snippet}\n\n{page.text}".strip()
            result.snippet = combined[: settings.brave_fetch_max_chars]
            if page.title and not result.title:
                result.title = page.title

    async def validate_key(self, api_key: str) -> BraveQuota | None:
        """Run one minimal query to prove a key works. Raises on failure."""
        key = (api_key or "").strip()
        if not key:
            raise SearchConfigError("Enter a Brave Search API key to test.")
        await self._search_one("test", key)
        return self.last_quota


brave_client = BraveClient()
