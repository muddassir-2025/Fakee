"""Pydantic contracts.

These mirror the two core documents in the plan:

* ``UserInput``       -> JSON 1: what the user knows (Groq extraction).
* ``Investigation``   -> JSON 2: what the investigation discovered.
* ``RiskAssessment``  -> explainable, deterministic risk result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "low", "medium", "high", "critical"]
RiskLevel = Literal["LOW", "MODERATE", "HIGH", "CRITICAL"]


# --------------------------------------------------------------------------
# JSON 1 — user-provided information, normalized by Groq
# --------------------------------------------------------------------------
class CompanyInfo(BaseModel):
    name: str | None = None
    website: str | None = None
    claimed_location: str | None = None


class OpportunityInfo(BaseModel):
    type: str | None = Field(default=None, description="job | internship | unknown")
    title: str | None = None
    salary: str | None = None
    location: str | None = None
    duration: str | None = None
    selection_process: str | None = None


class CommunicationChannels(BaseModel):
    whatsapp: bool = False
    telegram: bool = False
    email: bool = False
    phone: bool = False
    instagram: bool = False
    youtube: bool = False
    linkedin: bool = False


class MoneyRequest(BaseModel):
    detected: bool = False
    amount: str | None = None
    reason: str | None = None


class ContactPoints(BaseModel):
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class UserInput(BaseModel):
    company: CompanyInfo = Field(default_factory=CompanyInfo)
    opportunity: OpportunityInfo = Field(default_factory=OpportunityInfo)
    claims: list[str] = Field(default_factory=list)
    communication: CommunicationChannels = Field(default_factory=CommunicationChannels)
    money_request: MoneyRequest = Field(default_factory=MoneyRequest)
    contacts: ContactPoints = Field(default_factory=ContactPoints)
    raw_text: str = ""
    extraction_source: Literal["groq", "heuristic"] = "heuristic"


# --------------------------------------------------------------------------
# Investigation building blocks
# --------------------------------------------------------------------------
class DomainIntel(BaseModel):
    domain: str | None = None
    registration_date: str | None = None
    age_days: int | None = None
    registrar: str | None = None
    https_enabled: bool | None = None
    dns_resolves: bool | None = None
    tld: str | None = None
    cheap_tld: bool = False
    company_name_match: bool | None = None
    reachable: bool | None = None
    notes: list[str] = Field(default_factory=list)


class ReviewSignals(BaseModel):
    total_mentions: int = 0
    negative_mentions: int = 0
    payment_complaints: int = 0
    whatsapp_complaints: int = 0
    fake_interview_complaints: int = 0
    salary_complaints: int = 0
    nonpayment_complaints: int = 0
    source_urls: list[str] = Field(default_factory=list)


class DetectedPattern(BaseModel):
    pattern: str
    category: str
    detected: bool
    severity: Severity = "medium"
    confidence: float = 0.5
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    type: str = "web"
    source_url: str | None = None
    source_domain: str | None = None
    title: str | None = None
    summary: str = ""
    relevance: float = 0.5


class SearchQueryGroup(BaseModel):
    category: str
    queries: list[str]


class Investigation(BaseModel):
    company: CompanyInfo = Field(default_factory=CompanyInfo)
    opportunity: OpportunityInfo = Field(default_factory=OpportunityInfo)
    domain: DomainIntel = Field(default_factory=DomainIntel)
    reviews: ReviewSignals = Field(default_factory=ReviewSignals)
    detected_patterns: list[DetectedPattern] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    search_queries: list[SearchQueryGroup] = Field(default_factory=list)
    notable_findings: list[str] = Field(default_factory=list)
    sources_checked: int = 0
    structure_source: Literal["groq", "heuristic"] = "heuristic"


# --------------------------------------------------------------------------
# Risk assessment
# --------------------------------------------------------------------------
class RiskSignal(BaseModel):
    id: str
    label: str
    category: str
    severity: Severity
    weight: float
    points: float = Field(description="weight * confidence, rounded")
    confidence: float
    explanation: str
    evidence: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel
    headline: str
    summary: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[RiskSignal] = Field(default_factory=list)
    verified: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# API payloads
# --------------------------------------------------------------------------
class InvestigationRequest(BaseModel):
    text: str = Field(min_length=3)
    persist: bool = True


class PipelineStage(BaseModel):
    stage: str
    status: Literal["ok", "skipped", "failed"]
    detail: str = ""
    duration_ms: int = 0


class InvestigationResponse(BaseModel):
    id: str
    created_at: datetime
    status: str
    input: UserInput
    investigation: Investigation
    risk: RiskAssessment
    stages: list[PipelineStage] = Field(default_factory=list)
    historical_matches: int = 0
    duration_ms: int = 0


class InvestigationSummary(BaseModel):
    id: str
    created_at: datetime
    company_name: str | None
    opportunity_title: str | None
    risk_level: RiskLevel
    risk_score: int


class ReportRequest(BaseModel):
    text: str = Field(min_length=3)
    report_type: str = "user_report"
    source: str = "self_reported"
