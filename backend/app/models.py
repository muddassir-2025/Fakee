"""Database models — the intelligence database from the plan.

Tables: companies, opportunities, domains, investigations, detected_patterns,
web_evidence, risk_assessments, user_reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    name_normalized: Mapped[str] = mapped_column(String(255), index=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    claimed_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    opportunities: Mapped[list["Opportunity"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    company: Mapped[Company] = relationship(back_populates="opportunities")


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    tld: Mapped[str | None] = mapped_column(String(32), nullable=True)
    age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registration_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registrar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    https_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dns_resolves: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    company_name_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cheap_tld: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InvestigationRecord(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="completed")
    raw_input: Mapped[str] = mapped_column(Text)
    user_input: Mapped[dict] = mapped_column(JSON)
    investigation: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    patterns: Mapped[list["DetectedPatternRecord"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["WebEvidence"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class DetectedPatternRecord(Base):
    __tablename__ = "detected_patterns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    pattern_type: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    investigation: Mapped[InvestigationRecord] = relationship(back_populates="patterns")


class WebEvidence(Base):
    __tablename__ = "web_evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="web")
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_summary: Mapped[str] = mapped_column(Text, default="")
    evidence_type: Mapped[str] = mapped_column(String(64), default="mention")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    investigation: Mapped[InvestigationRecord] = relationship(back_populates="evidence")


class RiskAssessmentRecord(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserReport(Base):
    __tablename__ = "user_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    report_type: Mapped[str] = mapped_column(String(64), default="user_report")
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(128), default="self_reported")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
