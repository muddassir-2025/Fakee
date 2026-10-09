"""Persistence layer — the 'memory' of the detector.

Stores companies, opportunities, domains, investigations, patterns, evidence,
risk assessments and user reports, and answers historical-correlation queries.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Company,
    DetectedPatternRecord,
    Domain,
    InvestigationRecord,
    Opportunity,
    RiskAssessmentRecord,
    UserReport,
    WebEvidence,
)
from ..schemas import (
    Investigation,
    InvestigationSummary,
    RiskAssessment,
    UserInput,
)
from .text_utils import normalize_name


async def _get_or_create_company(session: AsyncSession, user_input: UserInput) -> Company | None:
    name = (user_input.company.name or "").strip()
    if not name:
        return None
    normalized = normalize_name(name)
    existing = (
        await session.execute(select(Company).where(Company.name_normalized == normalized))
    ).scalar_one_or_none()
    if existing:
        if user_input.company.website and not existing.website:
            existing.website = user_input.company.website
        return existing
    company = Company(
        name=name[:255],
        name_normalized=normalized[:255],
        website=(user_input.company.website or None),
        claimed_location=user_input.company.claimed_location,
    )
    session.add(company)
    await session.flush()
    return company


async def _get_or_create_opportunity(
    session: AsyncSession, company: Company | None, user_input: UserInput
) -> Opportunity | None:
    if company is None:
        return None
    title = (user_input.opportunity.title or "").strip() or None
    kind = user_input.opportunity.type
    if not title and not kind:
        return None
    existing = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.company_id == company.id,
                Opportunity.title == title,
            )
        )
    ).scalars().first()
    if existing:
        return existing
    opportunity = Opportunity(
        company_id=company.id,
        kind=kind,
        title=title[:255] if title else None,
        salary=user_input.opportunity.salary,
        location=user_input.opportunity.location,
        duration=user_input.opportunity.duration,
    )
    session.add(opportunity)
    await session.flush()
    return opportunity


async def _upsert_domain(session: AsyncSession, investigation: Investigation) -> None:
    d = investigation.domain
    if not d or not d.domain:
        return
    existing = (
        await session.execute(select(Domain).where(Domain.domain == d.domain))
    ).scalar_one_or_none()
    payload = dict(
        tld=d.tld,
        age_days=d.age_days,
        registration_date=d.registration_date,
        registrar=d.registrar,
        https_enabled=d.https_enabled,
        dns_resolves=d.dns_resolves,
        company_name_match=d.company_name_match,
        cheap_tld=d.cheap_tld,
        raw=d.model_dump(),
        checked_at=datetime.now(timezone.utc),
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
    else:
        session.add(Domain(domain=d.domain[:255], **payload))


async def count_historical_reports(
    session: AsyncSession, user_input: UserInput
) -> int:
    """Independent prior **reports** about this company.

    Only submissions a user explicitly made count. Earlier investigations by
    this tool are our own lookups and captured pages are evidence we gathered —
    neither is a report *about* the company. Counting them made a genuine
    employer look suspicious merely for having been checked before, and made the
    same posting score higher the second time it was run (a stored ADP
    investigation reached MODERATE purely from its own prior lookups). Reports
    are the signal; checking is not.
    """
    name = (user_input.company.name or "").strip()
    if not name:
        return 0
    normalized = normalize_name(name)
    if not normalized:
        return 0

    company = (
        await session.execute(
            select(Company).where(Company.name_normalized == normalized)
        )
    ).scalar_one_or_none()
    if company is None:
        return 0

    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(UserReport)
                .where(UserReport.company_id == company.id)
            )
        ).scalar()
        or 0
    )


async def persist_investigation(
    session: AsyncSession,
    *,
    user_input: UserInput,
    investigation: Investigation,
    risk: RiskAssessment,
    raw_text: str,
) -> InvestigationRecord:
    company = await _get_or_create_company(session, user_input)
    opportunity = await _get_or_create_opportunity(session, company, user_input)
    await _upsert_domain(session, investigation)

    record = InvestigationRecord(
        company_id=company.id if company else None,
        opportunity_id=opportunity.id if opportunity else None,
        status="completed",
        raw_input=raw_text,
        user_input=user_input.model_dump(),
        investigation=investigation.model_dump(),
        risk=risk.model_dump(),
    )
    session.add(record)
    await session.flush()

    for pattern in investigation.detected_patterns:
        if not pattern.detected:
            continue
        session.add(
            DetectedPatternRecord(
                investigation_id=record.id,
                company_id=company.id if company else None,
                pattern_type=pattern.pattern,
                category=pattern.category,
                severity=pattern.severity,
                confidence=pattern.confidence,
                evidence=pattern.evidence,
            )
        )

    for evidence in investigation.evidence[:40]:
        session.add(
            WebEvidence(
                investigation_id=record.id,
                company_id=company.id if company else None,
                source_url=evidence.source_url,
                source_domain=evidence.source_domain,
                source_type=evidence.type,
                title=evidence.title,
                content_summary=evidence.summary,
                evidence_type=evidence.type,
            )
        )

    session.add(
        RiskAssessmentRecord(
            investigation_id=record.id,
            company_id=company.id if company else None,
            score=risk.score,
            level=risk.level,
            confidence=risk.confidence,
            payload=risk.model_dump(),
        )
    )

    await session.commit()
    await session.refresh(record)
    return record


async def get_investigation(session: AsyncSession, investigation_id: str) -> InvestigationRecord | None:
    return (
        await session.execute(
            select(InvestigationRecord).where(InvestigationRecord.id == investigation_id)
        )
    ).scalar_one_or_none()


async def list_investigations(session: AsyncSession, limit: int = 20) -> list[InvestigationSummary]:
    rows = (
        await session.execute(
            select(InvestigationRecord).order_by(InvestigationRecord.created_at.desc()).limit(limit)
        )
    ).scalars().all()

    summaries: list[InvestigationSummary] = []
    for row in rows:
        risk = row.risk or {}
        summaries.append(
            InvestigationSummary(
                id=row.id,
                created_at=row.created_at,
                company_name=(row.user_input or {}).get("company", {}).get("name"),
                opportunity_title=(row.user_input or {}).get("opportunity", {}).get("title"),
                risk_level=risk.get("level", "MODERATE"),
                risk_score=int(risk.get("score", 0)),
            )
        )
    return summaries


async def create_user_report(
    session: AsyncSession,
    *,
    text: str,
    report_type: str,
    source: str,
) -> UserReport:
    # Best-effort company attribution from the free text.
    from .extraction import heuristic_extract

    user_input = heuristic_extract(text)
    company = await _get_or_create_company(session, user_input)
    report = UserReport(
        company_id=company.id if company else None,
        report_type=report_type,
        description=text,
        source=source,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def get_stats(session: AsyncSession) -> dict:
    investigations = int(
        (await session.execute(select(func.count()).select_from(InvestigationRecord))).scalar() or 0
    )
    reports = int((await session.execute(select(func.count()).select_from(UserReport))).scalar() or 0)
    companies = int((await session.execute(select(func.count()).select_from(Company))).scalar() or 0)
    by_level: dict[str, int] = {}
    rows = (
        await session.execute(
            select(RiskAssessmentRecord.level, func.count()).group_by(RiskAssessmentRecord.level)
        )
    ).all()
    for level, count in rows:
        by_level[level] = int(count)
    return {
        "investigations": investigations,
        "user_reports": reports,
        "companies": companies,
        "by_risk_level": by_level,
    }
