"""Persistence layer — the 'memory' of the detector.

Stores companies, opportunities, domains, investigations, patterns, evidence,
risk assessments and user reports, and answers historical-correlation queries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    REPORT_STATUS_APPROVED,
    REPORT_STATUS_PENDING,
    REPORT_STATUS_REJECTED,
    REPORT_STATUS_WITHDRAWN,
    REPORT_STATUSES,
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
    AdminReportOut,
    Investigation,
    InvestigationSummary,
    ReportOut,
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
                .where(UserReport.status.in_(counted_report_statuses()))
            )
        ).scalar()
        or 0
    )


def counted_report_statuses() -> tuple[str, ...]:
    """Which report statuses feed the historical-report signal.

    By default a fresh report counts immediately: a scam doing the rounds should
    warn the next applicant before an admin gets to it, and the admin can still
    reject it afterwards, which removes it again. Set
    ``REPORTS_REQUIRE_APPROVAL=true`` to hold every report in the queue instead.
    Withdrawn and rejected reports never count.
    """
    if settings.reports_require_approval:
        return (REPORT_STATUS_APPROVED,)
    return (REPORT_STATUS_PENDING, REPORT_STATUS_APPROVED)


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
    user_id: str | None = None,
    user_email: str | None = None,
    user_name: str | None = None,
    company_name: str | None = None,
    risk_level: str | None = None,
    risk_score: int | None = None,
) -> UserReport:
    """Store a report. Reporting requires sign-in, so the caller's identity is
    recorded with it — that is what makes the profile page and the admin
    dashboard possible."""
    # Best-effort company attribution from the free text.
    from .extraction import heuristic_extract

    user_input = heuristic_extract(text)
    company = await _get_or_create_company(session, user_input)
    resolved_name = (
        (company_name or "").strip()
        or (user_input.company.name or "").strip()
        or None
    )
    report = UserReport(
        company_id=company.id if company else None,
        report_type=report_type,
        description=text,
        source=source,
        user_id=user_id,
        user_email=user_email,
        user_name=user_name,
        company_name=resolved_name[:255] if resolved_name else None,
        risk_level=(risk_level or None),
        risk_score=risk_score,
        status=REPORT_STATUS_PENDING,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


# --------------------------------------------------------------- report views

def report_out(report: UserReport) -> ReportOut:
    """Serialize for the reporter. Never leaks another user's identity."""
    return ReportOut(
        id=report.id,
        created_at=report.created_at,
        updated_at=report.updated_at,
        status=report.status,  # type: ignore[arg-type]
        report_type=report.report_type,
        source=report.source,
        company_id=report.company_id,
        company_name=report.company_name,
        description=report.description,
        risk_level=report.risk_level,
        risk_score=report.risk_score,
        review_note=report.review_note,
        reviewed_at=report.reviewed_at,
    )


def admin_report_out(report: UserReport) -> AdminReportOut:
    """Serialize for an administrator: adds who filed it and who reviewed it."""
    base = report_out(report)
    return AdminReportOut(
        **base.model_dump(),
        user_id=report.user_id,
        user_email=report.user_email,
        user_name=report.user_name,
        reviewed_by=report.reviewed_by,
    )


async def count_reports_by_status(
    session: AsyncSession,
    *,
    user_id: str | None = None,
) -> dict[str, int]:
    """One count per status, zero-filled, optionally scoped to one reporter."""
    query = select(UserReport.status, func.count()).group_by(UserReport.status)
    if user_id is not None:
        query = query.where(UserReport.user_id == user_id)
    rows = (await session.execute(query)).all()
    counts = {status: 0 for status in REPORT_STATUSES}
    for status, count in rows:
        counts[status] = int(count)
    return counts


async def list_user_reports(session: AsyncSession, user_id: str) -> list[UserReport]:
    """A reporter's own reports, newest first."""
    return list(
        (
            await session.execute(
                select(UserReport)
                .where(UserReport.user_id == user_id)
                .order_by(UserReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_user_report(session: AsyncSession, report_id: str) -> UserReport | None:
    return (
        await session.execute(select(UserReport).where(UserReport.id == report_id))
    ).scalar_one_or_none()


async def withdraw_user_report(
    session: AsyncSession,
    report_id: str,
    *,
    user_id: str,
) -> tuple[UserReport | None, str]:
    """Take back one's own report. Returns ``(report, error)``.

    Ownership is checked against the stored ``user_id`` rather than the email,
    so a person who changes their Google address keeps control of their reports.
    """
    report = await get_user_report(session, report_id)
    if report is None:
        return None, "not_found"
    if report.user_id != user_id:
        return None, "not_owner"
    if report.status == REPORT_STATUS_WITHDRAWN:
        return report, "already_withdrawn"
    if report.status == REPORT_STATUS_REJECTED:
        return report, "rejected"
    report.status = REPORT_STATUS_WITHDRAWN
    report.reviewed_at = datetime.now(timezone.utc)
    report.review_note = "Withdrawn by the reporter."
    await session.commit()
    await session.refresh(report)
    return report, ""


async def list_admin_reports(
    session: AsyncSession,
    *,
    status_filter: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[UserReport], int]:
    """Every report, for the admin dashboard, newest first.

    Returns ``(page, total)`` where ``total`` is the size of the *filtered* set.
    """
    filters = []
    if status_filter:
        filters.append(UserReport.status == status_filter)
    if query:
        like = f"%{query.strip()}%"
        # Admins search by company, by the text itself, or by reporter — the
        # user id is included so everything one account filed can be pulled up
        # even after that person changes their email address.
        filters.append(
            UserReport.description.ilike(like)
            | UserReport.company_name.ilike(like)
            | UserReport.user_email.ilike(like)
            | UserReport.user_name.ilike(like)
            | UserReport.user_id.ilike(like)
        )

    total_query = select(func.count()).select_from(UserReport)
    page_query = select(UserReport)
    for condition in filters:
        total_query = total_query.where(condition)
        page_query = page_query.where(condition)

    total = int((await session.execute(total_query)).scalar() or 0)
    rows = (
        await session.execute(
            page_query.order_by(UserReport.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def review_user_report(
    session: AsyncSession,
    report_id: str,
    *,
    action: str,
    note: str | None,
    admin_email: str | None,
) -> tuple[UserReport | None, str]:
    """Accept, reject or re-queue a report. Returns ``(report, error)``."""
    report = await get_user_report(session, report_id)
    if report is None:
        return None, "not_found"
    if report.status == REPORT_STATUS_WITHDRAWN:
        # The reporter took it back; an admin cannot resurrect it.
        return report, "withdrawn"

    report.review_note = (note or "").strip() or None
    report.reviewed_by = admin_email
    report.reviewed_at = datetime.now(timezone.utc)
    report.status = {
        "approve": REPORT_STATUS_APPROVED,
        "reject": REPORT_STATUS_REJECTED,
        "reset": REPORT_STATUS_PENDING,
    }[action]
    await session.commit()
    await session.refresh(report)
    return report, ""


async def admin_overview(session: AsyncSession) -> dict:
    """The dashboard headline numbers, computed in the database."""
    counts = await count_reports_by_status(session)
    investigations = int(
        (await session.execute(select(func.count()).select_from(InvestigationRecord))).scalar() or 0
    )
    companies = int(
        (await session.execute(select(func.count()).select_from(Company))).scalar() or 0
    )
    reporters = int(
        (
            await session.execute(
                select(func.count(func.distinct(UserReport.user_id))).where(
                    UserReport.user_id.is_not(None)
                )
            )
        ).scalar()
        or 0
    )
    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = int(
        (
            await session.execute(
                select(func.count()).select_from(UserReport).where(UserReport.created_at >= since)
            )
        ).scalar()
        or 0
    )

    by_level: dict[str, int] = {}
    for level, count in (
        await session.execute(
            select(RiskAssessmentRecord.level, func.count()).group_by(RiskAssessmentRecord.level)
        )
    ).all():
        by_level[str(level)] = int(count)

    top_companies = [
        {"company": str(name), "reports": int(count)}
        for name, count in (
            await session.execute(
                select(UserReport.company_name, func.count())
                .where(UserReport.company_name.is_not(None))
                .group_by(UserReport.company_name)
                .order_by(func.count().desc())
                .limit(8)
            )
        ).all()
    ]

    return {
        "reports": counts,
        "investigations": investigations,
        "companies": companies,
        "users_reporting": reporters,
        "reports_last_7_days": recent,
        "by_risk_level": by_level,
        "top_companies": top_companies,
    }


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
