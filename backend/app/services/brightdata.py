"""Stage 3 — Bright Data search & scrape.

Real path uses Bright Data's SERP API and Web Unlocker. When no API key is
configured we produce clearly-labelled deterministic mock results so the whole
pipeline remains runnable and demoable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..schemas import SearchQueryGroup, UserInput
from .text_utils import extract_domain

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    query: str
    category: str
    title: str
    url: str
    snippet: str
    source_domain: str | None = None
    is_mock: bool = False

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "category": self.category,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_domain": self.source_domain,
            "is_mock": self.is_mock,
        }


@dataclass
class SearchBundle:
    results: list[SearchResult] = field(default_factory=list)
    used_mock: bool = False
    queries_run: int = 0


class BrightDataClient:
    def __init__(self) -> None:
        self.enabled = settings.brightdata_enabled
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.brightdata_base_url,
                timeout=settings.brightdata_timeout_seconds,
                headers={
                    "Authorization": f"Bearer {settings.brightdata_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _serp(self, query: str) -> list[SearchResult]:
        """Run one Google search through Bright Data's SERP zone."""
        client = await self._get_client()
        payload = {
            "zone": settings.brightdata_serp_zone,
            "url": f"https://www.google.com/search?q={httpx.QueryParams({'q': query})['q']}&hl=en&gl=in",
            "format": "json",
            "data_format": "parsed_light",
        }
        response = await client.post("/request", json=payload)
        response.raise_for_status()
        data = response.json()

        organic = data.get("organic") or data.get("organic_results") or []
        results: list[SearchResult] = []
        for item in organic[: settings.search_results_per_query]:
            url = item.get("link") or item.get("url") or ""
            results.append(
                SearchResult(
                    query=query,
                    category="",
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("description") or item.get("snippet") or "",
                    source_domain=extract_domain(url),
                )
            )
        return results

    async def fetch_page(self, url: str) -> str | None:
        """Fetch raw page HTML through the Web Unlocker zone."""
        if not self.enabled:
            return None
        try:
            client = await self._get_client()
            payload = {
                "zone": settings.brightdata_unlocker_zone,
                "url": url,
                "format": "raw",
            }
            response = await client.post("/request", json=payload)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bright Data fetch failed for %s: %s", url, exc)
            return None

    async def search_many(
        self, groups: list[SearchQueryGroup], user_input: UserInput
    ) -> SearchBundle:
        bundle = SearchBundle()
        if self.enabled:
            for group in groups:
                for query in group.queries:
                    try:
                        results = await self._serp(query)
                        bundle.queries_run += 1
                        for result in results:
                            result.category = group.category
                        bundle.results.extend(results)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("SERP query failed (%s): %s", query, exc)
            if bundle.results:
                return bundle

        # Fallback / mock mode.
        logger.info("Bright Data disabled or empty; using deterministic mock results.")
        bundle.used_mock = True
        bundle.results = mock_search(groups, user_input)
        bundle.queries_run = sum(len(g.queries) for g in groups)
        return bundle


def mock_search(
    groups: list[SearchQueryGroup], user_input: UserInput
) -> list[SearchResult]:
    """Deterministic, clearly-labelled synthetic search results.

    The mock mirrors whatever signals the user actually reported, so a
    fee-requesting WhatsApp "offer" surfaces complaint-style evidence and a
    plain, ordinary posting surfaces ordinary results. It never claims to be
    real data — every URL lives under ``mock.freebuff.dev``.
    """
    name = user_input.company.name or (user_input.contacts.domains[0] if user_input.contacts.domains else "the company")
    website = user_input.company.website or "the listed website"
    domain = user_input.contacts.domains[0] if user_input.contacts.domains else "unknown-domain"
    money = user_input.money_request
    comms = user_input.communication

    negative_available = (
        money.detected
        or comms.whatsapp
        or comms.telegram
        or bool(user_input.claims)
    )

    results: list[SearchResult] = []
    for group in groups:
        for query in group.queries:
            slug = abs(hash(query)) % 100000
            base = f"https://mock.freebuff.dev/{group.category}/{slug}"
            snippet, title = _mock_result_for(group.category, negative_available, name, website, domain, money, comms)
            results.append(
                SearchResult(
                    query=query,
                    category=group.category,
                    title=title,
                    url=base,
                    snippet=snippet,
                    source_domain="mock.freebuff.dev",
                    is_mock=True,
                )
            )
    return results


def _mock_result_for(
    category: str,
    negative_available: bool,
    name: str,
    website: str,
    domain: str,
    money,
    comms,
) -> tuple[str, str]:
    if category == "company_existence":
        return (
            f"{name} appears to operate a public presence. Company name association with "
            f"{website} could not be independently confirmed in this demo run.",
            f"{name} — company overview",
        )
    if category == "company_reviews":
        if negative_available:
            return (
                f"[DEMO DATA] Multiple reviewers describe a similar experience with {name}: "
                "recruitment over messaging apps and unexpected payment requests from candidates.",
                f"{name} employee & candidate reviews",
            )
        return (
            f"[DEMO DATA] A small number of neutral mentions of {name} were found. "
            "No strong reputation signal either way.",
            f"{name} reviews",
        )
    if category == "scam_complaints":
        if negative_available:
            fee = money.amount or "a fee"
            return (
                f"[DEMO DATA] Discussion thread warns candidates about {name}: applicants "
                f"reported being asked for {fee} after being \"selected\", and recruitment "
                "that happens entirely over chat apps.",
                f"Is {name} a scam? — discussion",
            )
        return (
            f"[DEMO DATA] No credible scam reports were located for {name} in this demo run.",
            f"{name} scam check",
        )
    if category == "job_complaints":
        if negative_available:
            return (
                f"[DEMO DATA] A candidate writes that {name} issued an offer without a "
                "technical interview and then requested an upfront payment to confirm the seat.",
                f"{name} hiring process complaints",
            )
        return (
            f"[DEMO DATA] Candidates describe a standard multi-round selection process for {name}.",
            f"{name} hiring process",
        )
    if category == "domain_mentions":
        return (
            f"[DEMO DATA] {domain} resolves and is linked to the {name} posting. "
            "Domain reputation is inconclusive in this demo run.",
            f"{domain} — domain reputation",
        )
    if category == "payment_complaints":
        if money.detected:
            return (
                f"[DEMO DATA] Post alleges {name} collects {'a ' + (money.reason or 'fee')} from "
                f"students ({money.amount or 'amount unspecified'}) before any onboarding.",
                f"{name} fee complaint",
            )
        return (
            f"[DEMO DATA] No payment-related complaints about {name} were found in this demo run.",
            f"{name} payment complaints",
        )
    if category == "channel_complaints":
        if comms.whatsapp or comms.telegram:
            channel = "WhatsApp" if comms.whatsapp else "Telegram"
            return (
                f"[DEMO DATA] Users report that {name} recruits mainly through {channel} and "
                "avoids formal application channels.",
                f"{name} {channel} recruitment reports",
            )
        return (
            f"[DEMO DATA] No unusual messaging-app recruitment reports for {name}.",
            f"{name} communication channels",
        )
    # specific_claims and anything else
    if negative_available and user_input_claims_present(money, comms):
        return (
            f"[DEMO DATA] Comment referencing {name} repeats a claim seen in the posting "
            "about guaranteed selection and instant joining.",
            f"{name} claim discussion",
        )
    return (
        f"[DEMO DATA] General result for {name}.",
        f"{name} — mention",
    )


def user_input_claims_present(money, comms) -> bool:
    return bool(money.detected or comms.whatsapp or comms.telegram)


brightdata_client = BrightDataClient()
