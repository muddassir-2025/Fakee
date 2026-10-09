"""The orchestrator — wires every stage together.

User input -> Groq extraction -> queries -> Exa search -> domain intel ->
Groq evidence structuring -> historical correlation -> pattern engine ->
risk engine -> persistence.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..schemas import (
    Investigation,
    InvestigationResponse,
    PipelineStage,
    UserInput,
)
from .cache import build_key, get_cached, store_cached
from .exa import SearchBundle, SearchResult, exa_client
from .domain_check import check_domain
from .evidence import structure_evidence
from .extraction import extract_user_input
from .patterns import detect_patterns
from .query_builder import build_queries
from .repository import count_historical_reports, persist_investigation
from .risk import assess

logger = logging.getLogger(__name__)


def _cached_response(payload: dict, timer: "_Timer", inv_id: str) -> InvestigationResponse:
    """Rebuild a response from a cached payload, flagged and re-staged."""
    payload = dict(payload)
    payload["cached"] = True
    payload["stages"] = [
        PipelineStage(stage="cache", status="skipped", detail="served from cache")
    ]
    payload["duration_ms"] = timer.elapsed_ms()
    return InvestigationResponse(**payload)


def _build_response(
    *,
    inv_id: str,
    user_input: UserInput,
    investigation: Investigation,
    risk,
    timer: "_Timer",
    historical: int,
) -> InvestigationResponse:
    from datetime import datetime, timezone

    return InvestigationResponse(
        id=inv_id,
        created_at=datetime.now(timezone.utc),
        status="completed",
        input=user_input,
        investigation=investigation,
        risk=risk,
        stages=timer.stages,
        historical_matches=historical,
        duration_ms=timer.elapsed_ms(),
    )


class _Timer:
    def __init__(self) -> None:
        self.stages: list[PipelineStage] = []
        self._t0 = time.perf_counter()

    def mark(self, stage: str, status: str, detail: str = "", started: float | None = None) -> None:
        duration = 0
        if started is not None:
            duration = int((time.perf_counter() - started) * 1000)
        self.stages.append(
            PipelineStage(stage=stage, status=status, detail=detail, duration_ms=duration)  # type: ignore[arg-type]
        )

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)


async def run_investigation(
    text: str,
    *,
    session: AsyncSession | None = None,
    persist: bool = True,
    investigation_id: str | None = None,
    user_input: UserInput | None = None,
    provided_results: list[SearchResult] | None = None,
    include_analyst: bool = True,
) -> InvestigationResponse:
    """Run the full pipeline.

    ``user_input`` and ``provided_results`` let a client that already did stage 1
    (extraction) and stage 3 (search/scrape — e.g. the browser extension) hand
    the server pre-gathered data so it is not fetched or extracted twice.

    ``include_analyst=False`` skips the bundled analyst read (frugal mode).
    """
    timer = _Timer()
    inv_id = investigation_id or uuid.uuid4().hex

    # 1. Extraction -------------------------------------------------------
    t = time.perf_counter()
    if user_input is not None:
        timer.mark("extraction", "skipped", "using client-provided structured input", t)
    else:
        user_input = await extract_user_input(text)
        timer.mark(
            "extraction",
            "ok",
            f"source={user_input.extraction_source}",
            t,
        )

    # 1b. Response cache --------------------------------------------------
    # A repeated investigation of the same company/domain is served from cache,
    # skipping search + Groq entirely. Only company-level data is stored.
    cache_key: str | None = None
    cache_enabled = session is not None and settings.response_cache_enabled
    if cache_enabled:
        cache_key = build_key(user_input)
        if cache_key:
            t_cache = time.perf_counter()
            cached_payload = await get_cached(session, cache_key)
            if cached_payload is not None:
                timer.mark("cache", "ok", "served from cache", t_cache)
                return _cached_response(cached_payload, timer, inv_id)

    # 2. Query generation -------------------------------------------------
    t = time.perf_counter()
    query_groups = build_queries(user_input)
    total_queries = sum(len(g.queries) for g in query_groups)
    timer.mark(
        "query_generation",
        "ok" if query_groups else "skipped",
        f"{total_queries} queries across {len(query_groups)} categories",
        t,
    )

    # 3. Web search / scrape ---------------------------------------------
    t = time.perf_counter()
    if provided_results:
        bundle = SearchBundle(results=list(provided_results), used_mock=False, queries_run=0)
        timer.mark(
            "web_search",
            "ok",
            f"{len(bundle.results)} pages captured by the client",
            t,
        )
    elif query_groups:
        bundle = await exa_client.search_many(query_groups, user_input)
        timer.mark(
            "web_search",
            "ok",
            f"{len(bundle.results)} results"
            + (" (mock/demo data)" if bundle.used_mock else " (live)"),
            t,
        )
    else:
        bundle = SearchBundle()
        timer.mark("web_search", "skipped", "no queries could be generated", t)

    # 4. Domain verification ---------------------------------------------
    t = time.perf_counter()
    domain_intel = await check_domain(user_input)
    detail = domain_intel.domain or "no domain provided"
    if domain_intel.age_days is not None:
        detail += f", age≈{domain_intel.age_days}d"
    timer.mark("domain_check", "ok" if domain_intel.domain else "skipped", detail, t)

    # 5. Evidence structuring --------------------------------------------
    t = time.perf_counter()
    reviews, evidence, findings, analyst, coverage = await structure_evidence(
        bundle.results, user_input, include_analyst=include_analyst
    )
    timer.mark(
        "evidence_structuring",
        "ok",
        f"{len(evidence)} evidence items, {reviews.total_mentions} mentions, "
        f"{coverage.pages_in_prompt}/{coverage.pages_captured} pages sent to model"
        + (f", analyst score {analyst.fraud_score}/100" if analyst else ""),
        t,
    )

    # 6. Historical correlation ------------------------------------------
    t = time.perf_counter()
    historical = 0
    if session is not None:
        try:
            historical = await count_historical_reports(session, user_input)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Historical lookup failed: %s", exc)
    timer.mark("history", "ok", f"{historical} prior records", t)

    # 7. Pattern detection -----------------------------------------------
    t = time.perf_counter()
    patterns = detect_patterns(
        user_input,
        domain_intel,
        reviews,
        historical_reports=historical,
        sources_checked=reviews.total_mentions,
    )
    timer.mark("pattern_detection", "ok", f"{len(patterns)} patterns detected", t)

    # 8. Risk assessment --------------------------------------------------
    t = time.perf_counter()
    risk = assess(
        patterns,
        user_input=user_input,
        evidence=evidence,
        sources_checked=reviews.total_mentions,
        used_mock=bundle.used_mock,
        domain=domain_intel,
        reviews=reviews,
        coverage=coverage,
    )
    if analyst is not None:
        risk = risk.model_copy(update={"analyst": analyst})
    timer.mark("risk_assessment", "ok", f"{risk.level} ({risk.score}/100)", t)

    investigation = Investigation(
        company=user_input.company,
        opportunity=user_input.opportunity,
        domain=domain_intel,
        reviews=reviews,
        detected_patterns=patterns,
        evidence=evidence,
        search_queries=query_groups,
        notable_findings=findings,
        sources_checked=reviews.total_mentions,
        structure_source="groq" if evidence and bundle.results and not bundle.used_mock else "heuristic",
        coverage=coverage,
    )

    # 9. Persistence ------------------------------------------------------
    t = time.perf_counter()
    if persist and session is not None:
        try:
            record = await persist_investigation(
                session,
                user_input=user_input,
                investigation=investigation,
                risk=risk,
                raw_text=text,
            )
            inv_id = record.id
            timer.mark("persistence", "ok", f"saved as {inv_id}", t)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Persistence failed")
            timer.mark("persistence", "failed", str(exc), t)
    else:
        timer.mark("persistence", "skipped", "persist disabled", t)

    # 10. Response ------------------------------------------------------
    response = _build_response(
        inv_id=inv_id,
        user_input=user_input,
        investigation=investigation,
        risk=risk,
        timer=timer,
        historical=historical,
    )

    # 11. Cache write ----------------------------------------------------
    # Only cache live results: a mock/demo result must never be served to a
    # later request, and caching it would make the demo sticky.
    if cache_enabled and cache_key and not bundle.used_mock:
        t_cache = time.perf_counter()
        try:
            await store_cached(session, cache_key, user_input=user_input, response=response)
            timer.mark("cache_store", "ok", "stored for reuse", t_cache)
            response = response.model_copy(update={"stages": timer.stages})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cache store failed: %s", exc)

    return response
