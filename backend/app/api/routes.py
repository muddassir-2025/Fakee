"""HTTP API."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, ping_db
from ..services import metrics
from .ratelimit import limit_investigations, limit_queries, limit_reports
from ..schemas import (
    AnalystRequest,
    AnalystResponse,
    EvidenceInvestigationRequest,
    InvestigationRequest,
    InvestigationResponse,
    InvestigationSummary,
    QueryPlan,
    QueryRequest,
    ReportRequest,
)
from ..services.exa import results_from_pages
from ..services.evidence import structure_evidence
from ..services.extraction import extract_user_input
from ..services.groq_client import groq_client
from ..services.patterns import pattern_metadata
from ..services.pipeline import run_investigation
from ..services.query_builder import build_queries
from ..services.repository import (
    create_user_report,
    get_investigation,
    get_stats,
    list_investigations,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _base_health() -> dict:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "integrations": {
            "groq": settings.groq_enabled,
            "exa": settings.exa_live,
            "database": "sqlite" if settings.is_sqlite else "postgresql",
        },
        "mock_mode": not (settings.groq_enabled and settings.exa_live),
    }


@router.get("/health/live")
async def health_live() -> dict:
    """Liveness: the process is up and serving (no dependency checks)."""
    return {"status": "ok", **_base_health()}


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness: dependencies (the database) are reachable."""
    db_ok = await ping_db()
    payload = {"status": "ok" if db_ok else "degraded", "database_ok": db_ok, **_base_health()}
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)


@router.get("/health")
async def health() -> JSONResponse:
    db_ok = await ping_db()
    payload = {
        "status": "ok" if db_ok else "degraded",
        "database_ok": db_ok,
        **_base_health(),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)


async def _run_stage(
    stage: str,
    text: str,
    *,
    session: AsyncSession,
    persist: bool,
    user_input=None,
    provided_results=None,
    include_analyst: bool = True,
) -> InvestigationResponse:
    """Run the pipeline for one request stage with timeout + metrics.

    ``stage`` is "api" (server-side search) or "extension" (client-provided
    evidence); it labels the metrics so the two paths can be compared.
    """
    start = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            run_investigation(
                text,
                session=session,
                persist=persist,
                user_input=user_input,
                provided_results=provided_results,
                include_analyst=include_analyst,
            ),
            timeout=settings.investigation_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        metrics.inc("investigations", {"stage": stage, "outcome": "timeout"})
        logger.warning("%s investigation timed out", stage)
        raise HTTPException(
            status_code=504, detail="Investigation timed out. Please try again."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        metrics.inc("investigations", {"stage": stage, "outcome": "error"})
        logger.exception("Investigation failed")
        detail = (
            f"Investigation failed: {exc}" if settings.verbose_errors else "Investigation failed."
        )
        raise HTTPException(status_code=500, detail=detail) from exc

    metrics.inc("investigations", {"stage": stage, "outcome": "success"})
    metrics.observe(
        "investigation_duration_seconds",
        time.perf_counter() - start,
        {"stage": stage},
    )
    return response


@router.post(
    "/investigate",
    response_model=InvestigationResponse,
    dependencies=[Depends(limit_investigations)],
)
async def investigate(
    payload: InvestigationRequest,
    session: AsyncSession = Depends(get_session),
) -> InvestigationResponse:
    text = payload.text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=422, detail="Please provide more text to investigate.")
    return await _run_stage(
        "api",
        text,
        session=session,
        persist=payload.persist,
        include_analyst=payload.include_analyst,
    )


@router.post("/queries", response_model=QueryPlan, dependencies=[Depends(limit_queries)])
async def plan_queries(payload: QueryRequest) -> QueryPlan:
    """Stage 1 only: extract structured input and return the queries to run.

    The browser extension calls this, runs the searches/scrapes itself (free),
    then posts the captured pages back to ``/investigate/with-evidence``.
    """
    text = payload.text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=422, detail="Please provide more text to investigate.")
    user_input = await extract_user_input(text)
    metrics.inc("query_plans")
    return QueryPlan(
        input=user_input,
        queries=build_queries(user_input),
        extraction_source=user_input.extraction_source,
        mock_mode=not settings.exa_live,
    )


@router.post(
    "/investigate/with-evidence",
    response_model=InvestigationResponse,
    dependencies=[Depends(limit_investigations)],
)
async def investigate_with_evidence(
    payload: EvidenceInvestigationRequest,
    session: AsyncSession = Depends(get_session),
) -> InvestigationResponse:
    """Run the pipeline over pages the client already captured."""
    text = payload.text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=422, detail="Please provide more text to investigate.")
    provided = results_from_pages(payload.pages)
    return await _run_stage(
        "extension",
        text,
        session=session,
        persist=payload.persist,
        user_input=payload.structured_input,
        provided_results=provided,
        include_analyst=payload.include_analyst,
    )


@router.get("/investigations", response_model=list[InvestigationSummary])
async def recent_investigations(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[InvestigationSummary]:
    return await list_investigations(session, limit=limit)


@router.get("/investigations/{investigation_id}", response_model=InvestigationResponse)
async def get_one(
    investigation_id: str,
    session: AsyncSession = Depends(get_session),
) -> InvestigationResponse:
    record = await get_investigation(session, investigation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return InvestigationResponse(
        id=record.id,
        created_at=record.created_at,
        status=record.status,
        input=record.user_input,
        investigation=record.investigation,
        risk=record.risk,
        stages=[],
        historical_matches=0,
        duration_ms=0,
    )


@router.post("/reports", dependencies=[Depends(limit_reports)])
async def submit_report(
    payload: ReportRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    report = await create_user_report(
        session,
        text=payload.text.strip(),
        report_type=payload.report_type,
        source=payload.source,
    )
    metrics.inc("user_reports", {"report_type": payload.report_type})
    return {"id": report.id, "status": "stored"}


@router.get("/groq/quota")
async def groq_quota() -> dict:
    """Last-known Groq rate-limit budget, captured from the provider's headers.

    Empty (only ``enabled``) until the first Groq call of the process, since the
    limits are only reported in response headers.
    """
    return groq_client.quota()


@router.post(
    "/analyst",
    dependencies=[Depends(limit_investigations)],
)
async def analyst_only(payload: AnalystRequest) -> AnalystResponse:
    """On-demand analyst read over evidence the client already captured.

    Lets a client run the investigation in frugal mode (``include_analyst=false``
    to save output tokens) and fetch the rated analyst read only when the user
    wants it. Costs one Groq request, and only when requested.
    """
    text = payload.text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=422, detail="Please provide more text to investigate.")
    user_input = payload.structured_input or await extract_user_input(text)
    provided = results_from_pages(payload.pages)
    reviews, evidence, findings, analyst, _coverage = await structure_evidence(
        provided, user_input, include_analyst=True
    )
    metrics.inc("analyst_requests")
    return AnalystResponse(
        analyst=analyst,
        reviews=reviews,
        evidence=evidence,
        notable_findings=findings,
    )


@router.get("/metrics")
async def metrics_json() -> dict:
    """Process-local operational metrics as JSON."""
    return metrics.snapshot()


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus() -> PlainTextResponse:
    """Same metrics in Prometheus text exposition format."""
    return PlainTextResponse(metrics.render_prometheus())


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)) -> dict:
    return await get_stats(session)


@router.get("/patterns")
async def patterns() -> list[dict[str, str]]:
    return pattern_metadata()
