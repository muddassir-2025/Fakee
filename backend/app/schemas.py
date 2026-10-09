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
# Kept separate from the risk score: a real posting can be expired, and an
# active posting can still be suspicious. "Verified" means supported by a
# trustworthy source (an authority, or the posting's own conclusive instruction).
OpportunityStatus = Literal[
    "FRAUDULENT_VERIFIED",
    "LEGITIMATE_VERIFIED",
    "LIKELY_LEGITIMATE_UNVERIFIED",
    "NEEDS_VERIFICATION",
    "EXPIRED",
    # The honest "we could not tell either way" outcome. Used when no evidence
    # was gathered and nothing in the posting itself is conclusive, so the
    # system must not imply the opportunity is safe.
    "INSUFFICIENT_EVIDENCE",
    # The evidence genuinely points both ways: the posting looks like it comes
    # from a real, verifiable employer, yet a serious fraud signal is present.
    "CONFLICTING_EVIDENCE",
]


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
    # Distinct source domains that produced a negative mention. Used to require
    # corroboration across independent sources rather than a raw count.
    negative_source_domains: list[str] = Field(default_factory=list)
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


class EvidenceCoverage(BaseModel):
    """How much captured evidence actually reached the analysis.

    Makes the basis of a verdict auditable: a result built on two pages must not
    look the same as one built on twenty. Pages analysed by the deterministic
    rules but never sent to the model are counted separately, because their
    signals are still folded in (see the evidence reconciliation).
    """

    pages_captured: int = 0
    # Pages sent to the model for structuring (0 when the model was skipped).
    pages_in_prompt: int = 0
    # Pages the deterministic rules examined but the model did not see.
    pages_rules_only: int = 0
    # Signal-bearing sentences selected from the captured pages.
    signal_sentences: int = 0
    # Whether any captured page was actually about this company.
    topical: bool = False
    # False when there was little or nothing to reason over.
    sufficient: bool = True
    note: str = ""


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
    # Transparency: how much of the captured evidence the verdict rests on.
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)


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


class TrustSignal(BaseModel):
    id: str
    label: str
    points: int
    explanation: str


class AnalystFlag(BaseModel):
    point: str
    evidence: str = ""


class AnalystReport(BaseModel):
    """LLM analyst's reasoned read of the gathered web evidence.

    Mirrors how a human investigator (or a search-then-reason agent) reports:
    a 0-100 fraud score, a verdict, red/green flags with their evidence, and
    what the candidate should do next. Kept alongside — not instead of — the
    deterministic score, so the verdict stays reproducible.
    """

    fraud_score: int = Field(default=0, ge=0, le=100)
    verdict: str = "needs_verification"
    summary: str = ""
    red_flags: list[AnalystFlag] = Field(default_factory=list)
    green_flags: list[AnalystFlag] = Field(default_factory=list)
    what_to_do: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel
    status: OpportunityStatus = "NEEDS_VERIFICATION"
    headline: str
    summary: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[RiskSignal] = Field(default_factory=list)
    # Positive side: what the posting gets right. Separate from the risk score.
    trust_score: int = Field(default=0, ge=0, le=100)
    trust_signals: list[TrustSignal] = Field(default_factory=list)
    verified: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    # "What you should verify before applying" — user-facing action list.
    checklist: list[str] = Field(default_factory=list)
    deadline: str | None = None
    expired: bool = False
    # AI analyst's reasoned read of the searched evidence (None without Groq).
    analyst: AnalystReport | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# API payloads
# --------------------------------------------------------------------------
class InvestigationRequest(BaseModel):
    text: str = Field(min_length=3, max_length=20000)
    # Storage is opt-in: a plain search must not create a company record. Only
    # an explicit report (POST /reports) persists anything.
    persist: bool = False
    # Cost knob: the analyst read is bundled into the same Groq call (so it is
    # not an extra request), but callers who don't need it can skip the tokens.
    include_analyst: bool = True


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
    # True when this result was served from the company-level response cache
    # instead of re-running the pipeline (a cost saving, not a downgrade).
    cached: bool = False


class InvestigationSummary(BaseModel):
    id: str
    created_at: datetime
    company_name: str | None
    opportunity_title: str | None
    risk_level: RiskLevel
    risk_score: int


class ReportRequest(BaseModel):
    text: str = Field(min_length=3, max_length=20000)
    report_type: str = "user_report"
    source: str = "self_reported"


# --------------------------------------------------------------------------
# Browser-extension capture (the client searches + scrapes; the server reasons)
# --------------------------------------------------------------------------
class ProvidedPage(BaseModel):
    """A page the client fetched and extracted, ready to be treated as evidence."""

    url: str = Field(max_length=2048)
    title: str = Field(default="", max_length=512)
    text: str = Field(default="", max_length=20000)
    query: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=64)


class QueryRequest(BaseModel):
    text: str = Field(min_length=3, max_length=20000)


class QueryPlan(BaseModel):
    """Stage-1 result the extension needs to run the searches itself."""

    input: UserInput
    queries: list[SearchQueryGroup]
    extraction_source: Literal["groq", "heuristic"]
    mock_mode: bool = False


class EvidenceInvestigationRequest(BaseModel):
    text: str = Field(min_length=3, max_length=20000)
    pages: list[ProvidedPage] = Field(default_factory=list, max_length=300)
    # Storage is opt-in: the browser extension searches without persisting, and
    # only the explicit report action stores a company record.
    persist: bool = False
    # Optional: the UserInput returned by /queries, to skip a second extraction.
    structured_input: UserInput | None = None
    # Cost knob: skip the bundled analyst read (fetch it later via /analyst).
    include_analyst: bool = True


class AnalystRequest(BaseModel):
    """On-demand analyst read over evidence the client already captured."""

    text: str = Field(min_length=3, max_length=20000)
    pages: list[ProvidedPage] = Field(default_factory=list, max_length=300)
    structured_input: UserInput | None = None


class AnalystResponse(BaseModel):
    analyst: AnalystReport | None = None
    reviews: ReviewSignals = Field(default_factory=ReviewSignals)
    evidence: list[Evidence] = Field(default_factory=list)
    notable_findings: list[str] = Field(default_factory=list)
