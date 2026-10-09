"""Stage 3 — Exa web search & content.

Real path uses Exa's Search API (free tier: no credit card, monthly credits) to
gather public results, and the Contents API to pull page text on demand. When no
API key is configured we produce clearly-labelled deterministic mock results so
the whole pipeline remains runnable and demoable.

Exa API: POST https://api.exa.ai/search  (Authorization: Bearer $EXA_API_KEY)

Mock results carry an explicit ``sentiment`` so downstream classification does
not have to infer tone from polite, negation-heavy sentences.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..schemas import ProvidedPage, SearchQueryGroup, UserInput
from .text_utils import extract_domain

logger = logging.getLogger(__name__)

# Statuses worth retrying: rate limits and transient upstream failures.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class SearchResult:
    query: str
    category: str
    title: str
    url: str
    snippet: str
    source_domain: str | None = None
    is_mock: bool = False
    sentiment: str | None = None  # "negative" | "neutral" | "positive" | None

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "category": self.category,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_domain": self.source_domain,
            "is_mock": self.is_mock,
            "sentiment": self.sentiment,
        }


@dataclass
class SearchBundle:
    results: list[SearchResult] = field(default_factory=list)
    used_mock: bool = False
    queries_run: int = 0


class ExaClient:
    def __init__(self) -> None:
        self.enabled = settings.exa_live
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.exa_base_url,
                timeout=settings.exa_timeout_seconds,
                headers={
                    "Authorization": f"Bearer {settings.exa_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
        """Exponential backoff with jitter, honouring a Retry-After header."""
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                try:
                    return max(0.0, min(30.0, float(header)))
                except ValueError:
                    pass
        base = settings.exa_backoff_base_seconds
        return min(20.0, base * (2 ** attempt)) + random.uniform(0, 0.5)

    async def _post_with_retry(self, path: str, payload: dict) -> httpx.Response:
        """POST with exponential backoff on 429/5xx and transport errors."""
        client = await self._get_client()
        attempts = max(1, settings.exa_max_retries + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await client.post(path, json=payload)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(self._retry_delay(attempt, None))
                continue
            if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
                await asyncio.sleep(self._retry_delay(attempt, response))
                continue
            return response
        raise last_exc or RuntimeError("Exa request failed")

    async def _search(self, query: str) -> list[SearchResult]:
        """Run one web search through Exa."""
        payload = {
            "query": query,
            "type": settings.exa_search_type,
            "numResults": settings.search_results_per_query,
            "contents": {"highlights": True},
        }
        response = await self._post_with_retry("/search", payload)
        response.raise_for_status()
        data = response.json()

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[: settings.search_results_per_query]:
            url = item.get("url") or ""
            highlights = [h for h in (item.get("highlights") or []) if h]
            snippet = " ".join(highlights) if highlights else (item.get("text") or "")
            results.append(
                SearchResult(
                    query=query,
                    category="",
                    title=item.get("title") or "",
                    url=url,
                    snippet=snippet.strip()[:600],
                    source_domain=extract_domain(url),
                )
            )
        return results

    async def fetch_page(self, url: str) -> str | None:
        """Fetch page text through Exa's Contents API."""
        if not self.enabled:
            return None
        try:
            payload = {"urls": [url], "text": {"maxCharacters": 8000}}
            response = await self._post_with_retry("/contents", payload)
            response.raise_for_status()
            results = response.json().get("results") or []
            if results:
                return results[0].get("text")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa content fetch failed for %s: %s", url, exc)
            return None

    async def search_many(
        self, groups: list[SearchQueryGroup], user_input: UserInput
    ) -> SearchBundle:
        bundle = SearchBundle()
        if self.enabled:
            # Run queries with bounded concurrency: fast, but gentle on the API
            # and the free-tier rate limit.
            semaphore = asyncio.Semaphore(max(1, settings.exa_max_concurrency))

            async def run(category: str, query: str) -> tuple[str, str, list[SearchResult] | None]:
                async with semaphore:
                    try:
                        return category, query, await self._search(query)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Exa query failed (%s): %s", query, exc)
                        return category, query, None

            jobs = [(g.category, q) for g in groups for q in g.queries]
            outcomes = await asyncio.gather(*(run(c, q) for c, q in jobs))
            for category, _query, results in outcomes:
                if not results:
                    continue
                bundle.queries_run += 1
                for result in results:
                    result.category = category
                bundle.results.extend(results)
            if bundle.results:
                return bundle

        # Fallback / mock mode.
        logger.info("Exa disabled or empty; using deterministic mock results.")
        bundle.used_mock = True
        bundle.results = mock_search(groups, user_input)
        bundle.queries_run = sum(len(g.queries) for g in groups)
        return bundle


def results_from_pages(pages: list[ProvidedPage]) -> list[SearchResult]:
    """Adapt pages the browser captured into the pipeline's ``SearchResult`` shape.

    The extension performs the search and scraping client-side; from this point
    on the server treats the captured pages exactly like provider results.
    """
    results: list[SearchResult] = []
    seen: set[str] = set()
    for page in pages:
        url = (page.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                query=page.query or "",
                category=page.category or "browser_capture",
                title=(page.title or "")[:300],
                url=url,
                snippet=(page.text or "").strip()[:4000],
                source_domain=extract_domain(url),
                is_mock=False,
                sentiment=None,
            )
        )
    return results


def _has_scam_signals(user_input: UserInput) -> bool:
    """Only treat the posting as risky when the user actually reported risk."""
    money = user_input.money_request
    comms = user_input.communication
    return bool(money.detected or comms.whatsapp or comms.telegram)


def mock_search(
    groups: list[SearchQueryGroup], user_input: UserInput
) -> list[SearchResult]:
    """Deterministic, clearly-labelled synthetic search results.

    The mock mirrors whatever signals the user actually reported, so a
    fee-demanding WhatsApp "offer" surfaces complaint-style evidence while a
    plain, ordinary posting surfaces ordinary results. Every URL lives under
    ``mock.freebuff.dev`` so it can never be mistaken for real data.
    """
    name = (
        user_input.company.name
        or (user_input.contacts.domains[0] if user_input.contacts.domains else "the company")
    )
    website = user_input.company.website or "the listed website"
    domain = user_input.contacts.domains[0] if user_input.contacts.domains else "unknown-domain"
    money = user_input.money_request
    comms = user_input.communication
    risky = _has_scam_signals(user_input)

    results: list[SearchResult] = []
    for group in groups:
        for query in group.queries:
            digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
            url = f"https://mock.freebuff.dev/{group.category}/{digest}"
            snippet, title, sentiment = _mock_result_for(
                group.category, risky, name, website, domain, money, comms
            )
            results.append(
                SearchResult(
                    query=query,
                    category=group.category,
                    title=title,
                    url=url,
                    snippet=snippet,
                    source_domain="mock.freebuff.dev",
                    is_mock=True,
                    sentiment=sentiment,
                )
            )
    return results


def _mock_result_for(
    category: str,
    risky: bool,
    name: str,
    website: str,
    domain: str,
    money,
    comms,
) -> tuple[str, str, str]:
    """Return (snippet, title, sentiment)."""
    if category == "company_existence":
        return (
            f"{name} appears to maintain a public presence. In this demo run the "
            f"association with {website} could not be independently confirmed.",
            f"{name} — company overview",
            "neutral",
        )
    if category == "company_reviews":
        if risky:
            return (
                f"Reviewers describe a similar experience with {name}: recruitment "
                "conducted over messaging apps, and payment requested from candidates.",
                f"{name} employee & candidate reviews",
                "negative",
            )
        return (
            f"A small number of neutral mentions of {name} were found. No strong "
            "reputation signal either way.",
            f"{name} reviews",
            "neutral",
        )
    if category == "scam_complaints":
        if risky:
            fee = money.amount or "a fee"
            return (
                f"Discussion thread warns candidates about {name}: applicants were asked "
                f"for {fee} after being \"selected\", with all recruitment over chat apps.",
                f"Is {name} a scam? — discussion",
                "negative",
            )
        return (
            f"No credible scam or fraud discussion involving {name} was located.",
            f"{name} scam check",
            "neutral",
        )
    if category == "job_complaints":
        if risky:
            return (
                f"A candidate writes that {name} extended an offer with no technical "
                "interview, then requested an upfront payment to confirm the seat.",
                f"{name} hiring process complaints",
                "negative",
            )
        return (
            f"Candidates describe a standard multi-round selection process for {name}.",
            f"{name} hiring process",
            "neutral",
        )
    if category == "domain_mentions":
        return (
            f"{domain} resolves and is linked to the {name} posting. Domain reputation "
            "is inconclusive in this demo run.",
            f"{domain} — domain reputation",
            "neutral",
        )
    if category == "payment_complaints":
        if money.detected:
            return (
                f"Post alleges {name} collects a {money.reason or 'fee'} from students "
                f"({money.amount or 'amount unspecified'}) before any onboarding.",
                f"{name} fee allegation",
                "negative",
            )
        return (
            f"No fee or payment allegations against {name} were surfaced.",
            f"{name} payment check",
            "neutral",
        )
    if category == "channel_complaints":
        if comms.whatsapp or comms.telegram:
            channel = "WhatsApp" if comms.whatsapp else "Telegram"
            return (
                f"Users report that {name} recruits mainly through {channel} and avoids "
                "formal application channels.",
                f"{name} {channel} recruitment reports",
                "negative",
            )
        return (
            f"No unusual messaging-app recruitment reports for {name}.",
            f"{name} communication channels",
            "neutral",
        )
    # specific_claims and anything else
    if risky:
        return (
            f"A comment referencing {name} repeats a claim from the posting about "
            "guaranteed selection and instant joining.",
            f"{name} claim discussion",
            "negative",
        )
    return (f"General result for {name}.", f"{name} — mention", "neutral")


exa_client = ExaClient()
