"""Stage 7 — the risk engine.

Deterministic scoring. We deliberately do NOT let the LLM decide the final
verdict: the model extracts evidence, and this module combines signals into a
reproducible, explainable score.
"""

from __future__ import annotations

from datetime import date

from ..schemas import (
    DetectedPattern,
    DomainIntel,
    Evidence,
    EvidenceCoverage,
    OpportunityStatus,
    ReviewSignals,
    RiskAssessment,
    RiskLevel,
    RiskSignal,
    TrustSignal,
    UserInput,
)
from .deadline import DeadlineInfo, detect_deadline
from .patterns import CONCLUSIVE_FRAUD_PATTERNS
from .text_utils import is_shortener
from .trust import assess_trust

SEVERITY_WEIGHT: dict[str, float] = {
    "info": 0.05,
    "low": 0.12,
    "medium": 0.28,
    "high": 0.48,
    "critical": 0.72,
}

# Co-occurrence amplifiers: these combinations are far more dangerous than the
# sum of their parts, which is the whole point of pattern-based detection.
COMBINATIONS: list[tuple[set[str], float, str]] = [
    (
        {"upfront_payment", "no_interview_selection"},
        14.0,
        "A fee demanded alongside selection without an interview is a textbook advance-fee scam.",
    ),
    (
        {"upfront_payment", "whatsapp_recruitment"},
        10.0,
        "Money requested through a WhatsApp-only process compounds the risk.",
    ),
    (
        {"upfront_payment", "recent_domain"},
        12.0,
        "A payment request on a recently registered website is a strong fraud indicator.",
    ),
    (
        {"upfront_payment", "suspicious_reward_offer"},
        10.0,
        "Paying to claim a 'free' reward is a classic bait structure.",
    ),
    (
        {"no_company_footprint", "whatsapp_recruitment", "upfront_payment"},
        15.0,
        "No verifiable company, WhatsApp-only contact and payment demand together are conclusive.",
    ),
    (
        {"sensitive_data_request", "upfront_payment"},
        14.0,
        "Sensitive data plus payment requests indicate a high-risk identity/financial scam.",
    ),
    (
        {"repeated_payment_complaints", "upfront_payment"},
        10.0,
        "Independent complainants corroborate the payment demand reported here.",
    ),
    (
        {"guaranteed_selection", "unusually_high_salary"},
        8.0,
        "Guaranteed selection for unusually high pay is an unrealistic-opportunity pattern.",
    ),
    (
        {"domain_name_mismatch", "recent_domain"},
        8.0,
        "A new domain that does not match the claimed brand suggests impersonation.",
    ),
    (
        {"historical_reports", "upfront_payment"},
        10.0,
        "This entity has prior reports and again shows payment demands.",
    ),
]

# Signals that represent *concrete* evidence of fraud: money or credentials
# changing hands, impersonation / infrastructure evidence, or independent
# corroboration. Everything else (contact over WhatsApp/Telegram, urgency, a
# single weak reputation mention) is contextual.
#
# Real-world data from Indian job/internship scams (cybercrime.gov.in advisories,
# bank/PSU fraud-awareness posts, scam-awareness write-ups) shows that genuine
# recruiters also use WhatsApp and Telegram, so messaging-app contact alone is
# NOT evidence of fraud. A HIGH or CRITICAL verdict therefore requires at least
# one hard signal; contextual signals alone can only ever reach MODERATE.
HARD_PATTERNS: frozenset[str] = frozenset(
    {
        "upfront_payment",
        "untraceable_payment",
        "suspicious_reward_offer",
        "sensitive_data_request",
        "recent_domain",
        "domain_name_mismatch",
        "domain_unreachable",
        "no_company_footprint",
        "repeated_payment_complaints",
        "historical_reports",
        # Concrete scam structures that are not merely contextual.
        "unrealistic_easy_money",
        "reshipping_scheme",
    }
)

LEVELS: list[tuple[int, RiskLevel]] = [
    (70, "CRITICAL"),
    (45, "HIGH"),
    (20, "MODERATE"),
    (0, "LOW"),
]

HEADLINES: dict[RiskLevel, str] = {
    "CRITICAL": "Very likely a fake / fraudulent opportunity",
    "HIGH": "High risk — multiple serious warning signs",
    "MODERATE": "Some caution advised — unverified signals present",
    "LOW": "No strong fraud indicators detected",
}

# The honest "we could not tell either way" outcome. Shown instead of a LOW
# headline when no usable evidence was gathered, so the result never implies
# the opportunity is safe merely because nothing was found.
INSUFFICIENT_HEADLINE = "Not enough evidence to judge either way"
INSUFFICIENT_RECOMMENDATION = (
    "We could not gather enough evidence to judge this opportunity. Treat it as "
    "unverified: confirm the role through the employer's own official channel "
    "before acting, and never pay a fee or share sensitive data."
)

# The evidence genuinely disagrees with itself: strong identity/trust signals
# point to a real employer, while a serious fraud signal is also present. Said
# plainly rather than silently picking a side.
CONFLICTING_HEADLINE = "Evidence conflicts — verify before trusting"
CONFLICTING_RECOMMENDATION = (
    "The evidence points both ways: this may genuinely come from the named "
    "employer, yet a serious fraud signal is also present. Do not pay or share "
    "sensitive data, and confirm the exact role directly through the "
    "employer's official channel that you open yourself."
)

# Trust signals that assert *identity* — that the posting really comes from the
# named employer. Generic positives (no fee mentioned, clear eligibility) can
# appear on a scam too, so they do not by themselves create a conflict.
IDENTITY_TRUST_SIGNALS = frozenset(
    {
        "official_source_found",
        "corporate_email_matches_employer",
        "application_domain_matches_employer",
    }
)
# How much positive evidence is needed before trust is considered "strong".
STRONG_TRUST_SCORE = 60

RECOMMENDATIONS: dict[RiskLevel, str] = {
    "CRITICAL": (
        "Do not pay any money, do not share banking/OTP/identity documents, and stop "
        "contact. Report the sender to your placement cell and the cybercrime portal."
    ),
    "HIGH": (
        "Treat this as unsafe. Verify the employer through an official channel you "
        "initiate yourself, and never transfer money or share sensitive data."
    ),
    "MODERATE": (
        "Proceed cautiously. Independently verify the employer via its official "
        "website, and never pay a fee to be considered for a role."
    ),
    "LOW": (
        "No strong indicators were found, but always apply through the employer's "
        "official careers page and never pay for a job or internship."
    ),
}

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _level_for(score: int) -> RiskLevel:
    for threshold, level in LEVELS:
        if score >= threshold:
            return level
    return "LOW"


def assess(
    patterns: list[DetectedPattern],
    *,
    user_input: UserInput,
    evidence: list[Evidence],
    sources_checked: int,
    used_mock: bool,
    domain: DomainIntel | None = None,
    reviews: ReviewSignals | None = None,
    coverage: EvidenceCoverage | None = None,
    today: date | None = None,
) -> RiskAssessment:
    signals: list[RiskSignal] = []
    raw_score = 0.0

    patterns_by_id = {p.pattern: p for p in patterns if p.detected}

    for p in patterns_by_id.values():
        weight = SEVERITY_WEIGHT.get(p.severity, 0.3)
        points = round(weight * p.confidence * 100, 1)
        raw_score += points
        signals.append(
            RiskSignal(
                id=p.pattern,
                label=_label(p.pattern),
                category=p.category,
                severity=p.severity,
                weight=weight,
                points=points,
                confidence=p.confidence,
                explanation=p.rationale,
                evidence=p.evidence,
            )
        )

    # Co-occurrence amplifiers.
    present = set(patterns_by_id)
    for combo, bonus, explanation in COMBINATIONS:
        if combo.issubset(present):
            raw_score += bonus
            signals.append(
                RiskSignal(
                    id="combination:" + "+".join(sorted(combo)),
                    label="Dangerous signal combination",
                    category="combination",
                    severity="critical" if bonus >= 12 else "high",
                    weight=round(bonus / 100, 3),
                    points=bonus,
                    confidence=0.9,
                    explanation=explanation,
                    evidence=[],
                )
            )

    score = int(min(100, round(raw_score)))
    level = _level_for(score)

    # Gate: contextual signals (messaging-app contact, urgency, an isolated weak
    # reputation mention) cannot, by themselves, justify a HIGH/CRITICAL verdict.
    # Anything at critical severity — including corroborated scam reports — is
    # treated as hard evidence and may drive a HIGH verdict.
    hard = any(p in HARD_PATTERNS for p in present) or any(
        patterns_by_id[p].severity == "critical" for p in present
    )
    if level in {"HIGH", "CRITICAL"} and not hard:
        level = "MODERATE"
        score = min(score, 44)

    # Whether there is anything meaningful to judge at all. This can never mask
    # a detection: a hard signal short-circuits it (see the helper).
    insufficient = _insufficient_evidence(
        hard=hard, coverage=coverage, sources_checked=sources_checked
    )

    # Confidence reflects the breadth and quality of evidence gathered.
    if used_mock:
        confidence = 0.45
    elif sources_checked >= 20:
        confidence = 0.9
    elif sources_checked >= 8:
        confidence = 0.75
    elif sources_checked > 0:
        confidence = 0.6
    else:
        confidence = 0.4
    if patterns_by_id:
        confidence = min(0.95, confidence + 0.05)

    signals.sort(key=lambda s: (SEVERITY_RANK.get(s.severity, 0), s.points), reverse=True)

    verified, unverified = _verification_lists(patterns_by_id, user_input, sources_checked, used_mock)

    # Positive evidence, deadline/expiry, and the resulting opportunity status.
    deadline_info = detect_deadline(user_input.raw_text, today)
    trust_score, trust_signals = assess_trust(
        user_input, domain=domain, reviews=reviews, sources_checked=sources_checked
    )
    conflicting = _conflicting_evidence(
        present=patterns_by_id,
        trust_score=trust_score,
        trust_signals=trust_signals,
        insufficient=insufficient,
    )
    status = _status_for(
        patterns_by_id,
        level=level,
        trust_score=trust_score,
        domain=domain,
        expired=deadline_info.expired,
        insufficient=insufficient,
        conflicting=conflicting,
    )
    checklist = _checklist(patterns_by_id, user_input, deadline_info, trust_signals)
    summary = _summary(status, level, signals, user_input)

    # A defensive floor on confidence: an inconclusive result must not sound
    # certain, and it must not be dressed up as a clean one either.
    if status == "INSUFFICIENT_EVIDENCE":
        confidence = min(confidence, 0.3)

    return RiskAssessment(
        score=score,
        level=level,
        status=status,
        headline=_headline_for(status, level),
        summary=summary,
        recommendation=_recommendation_for(status, level),
        confidence=round(confidence, 2),
        signals=signals,
        trust_score=trust_score,
        trust_signals=trust_signals,
        verified=verified,
        unverified=unverified,
        checklist=checklist,
        deadline=deadline_info.raw,
        expired=deadline_info.expired,
        evidence=evidence[:20],
        source_urls=[e.source_url for e in evidence if e.source_url][:20],
    )


def _headline_for(status: OpportunityStatus, level: RiskLevel) -> str:
    if status == "INSUFFICIENT_EVIDENCE":
        return INSUFFICIENT_HEADLINE
    if status == "CONFLICTING_EVIDENCE":
        return CONFLICTING_HEADLINE
    return HEADLINES[level]


def _recommendation_for(status: OpportunityStatus, level: RiskLevel) -> str:
    if status == "INSUFFICIENT_EVIDENCE":
        return INSUFFICIENT_RECOMMENDATION
    if status == "CONFLICTING_EVIDENCE":
        return CONFLICTING_RECOMMENDATION
    return RECOMMENDATIONS[level]


def _conflicting_evidence(
    *,
    present: dict[str, DetectedPattern],
    trust_score: int,
    trust_signals: list[TrustSignal],
    insufficient: bool,
) -> bool:
    """True when strong identity trust and a serious fraud signal both appear.

    Requires a *serious* signal (high/critical), so a lone weak flag — a single
    prior report, a cheap TLD — does not manufacture a conflict. This never
    suppresses a detection: the risk level still reflects the fraud signals, it
    only reports that the two bodies of evidence disagree.
    """
    if insufficient:
        return False
    serious = any(
        (pattern_id in HARD_PATTERNS or detected.severity == "critical")
        and SEVERITY_RANK.get(detected.severity, 0) >= SEVERITY_RANK["high"]
        for pattern_id, detected in present.items()
    )
    if not serious:
        return False
    if trust_score < STRONG_TRUST_SCORE:
        return False
    return any(s.id in IDENTITY_TRUST_SIGNALS for s in trust_signals)


def _insufficient_evidence(
    *, hard: bool, coverage: EvidenceCoverage | None, sources_checked: int
) -> bool:
    """True when there is genuinely nothing to judge.

    Requires *both* that no hard signal was found and that the gathered evidence
    is unusable: either nothing was examined, or coverage reported that no
    captured page was even about this company. A hard or conclusive signal
    always wins, so this can never mask a detection.
    """
    if hard:
        return False
    if coverage is not None:
        return not coverage.sufficient
    return sources_checked == 0


def _status_for(
    present: dict[str, DetectedPattern],
    *,
    level: RiskLevel,
    trust_score: int,
    domain: DomainIntel | None,
    expired: bool,
    insufficient: bool,
    conflicting: bool,
) -> OpportunityStatus:
    """Map signals into a status, deliberately kept separate from the score.

    Fraud is only "verified" when the posting itself conclusively instructs the
    candidate to pay, hand over credentials, or launder a fake cheque. Expiry is
    orthogonal and reported before any legitimacy claim. An inconclusive run is
    reported before any legitimacy claim, but never before a detection. When
    strong identity trust and a serious fraud signal coexist, that disagreement
    is reported as a conflict rather than silently resolved either way.
    Legitimacy is only "verified" when a live domain matches the named employer
    and the risk is low.
    """
    if CONCLUSIVE_FRAUD_PATTERNS & set(present):
        return "FRAUDULENT_VERIFIED"
    if expired:
        return "EXPIRED"
    if insufficient:
        return "INSUFFICIENT_EVIDENCE"
    if conflicting:
        return "CONFLICTING_EVIDENCE"
    domain_evidence = bool(domain and domain.company_name_match)
    if level == "LOW" and trust_score >= 60 and domain_evidence:
        return "LEGITIMATE_VERIFIED"
    if level == "LOW" and trust_score >= 30:
        return "LIKELY_LEGITIMATE_UNVERIFIED"
    return "NEEDS_VERIFICATION"


def _checklist(
    present: dict[str, DetectedPattern],
    user_input: UserInput,
    deadline_info: DeadlineInfo,
    trust_signals: list[TrustSignal],
) -> list[str]:
    """User-facing "what to verify before applying" list."""
    ids = set(present)
    items: list[str] = []

    if {"upfront_payment", "untraceable_payment", "fake_check_payment"} & ids:
        items.append(
            "Do not pay anything — no genuine employer charges a fee to hire, train or onboard."
        )
    if "sensitive_data_request" in ids:
        items.append("Never share OTP, PIN, CVV, passwords or Aadhaar/PAN/bank details.")
    if "shortener_link" in ids or any(is_shortener(d) for d in user_input.contacts.domains):
        items.append(
            "Expand the shortened link and confirm it resolves to the employer's own domain."
        )
    if {"domain_name_mismatch", "recent_domain", "domain_unreachable"} & ids:
        items.append(
            "Open the employer's official careers page yourself and confirm this exact role exists there."
        )
    else:
        items.append(
            "Open the employer's official careers page yourself and search for this exact role."
        )
    items.append("Check that the recruiter's email domain matches the employer before replying.")
    if deadline_info.expired:
        items.append("The stated deadline has passed — confirm whether a new opening exists.")
    elif deadline_info.deadline is not None and deadline_info.raw:
        items.append(f"Apply well before the stated deadline ({deadline_info.raw}).")
    if any(s.id == "no_money_requested" for s in trust_signals):
        items.append("No upfront payment was requested — never pay later either.")
    return items


def _label(pattern: str) -> str:
    from .patterns import PATTERN_CATALOG

    meta = PATTERN_CATALOG.get(pattern)
    return meta["label"] if meta else pattern


def _verification_lists(
    present: dict[str, DetectedPattern],
    user_input: UserInput,
    sources_checked: int,
    used_mock: bool,
) -> tuple[list[str], list[str]]:
    verified: list[str] = []
    unverified: list[str] = []

    if "recent_domain" in present:
        verified.append("Website domain appears recently registered.")
    if "domain_name_mismatch" in present:
        verified.append("Domain does not match the claimed company name.")
    if "domain_unreachable" in present:
        verified.append("Claimed website could not be resolved/reached.")
    if "repeated_payment_complaints" in present:
        verified.append("Multiple independent sources corroborate payment complaints.")
    if "historical_reports" in present:
        verified.append("Prior reports exist in this platform's database.")
    if sources_checked:
        verified.append(f"{sources_checked} web sources were examined.")

    if user_input.company.name and "no_company_footprint" in present:
        unverified.append(f"No independent confirmation that '{user_input.company.name}' exists as described.")
    if "domain_name_mismatch" not in present and user_input.company.website and "recent_domain" not in present:
        unverified.append("Whether the website is officially operated by the company.")
    unverified.append("Whether the offer is officially authorised by the named organisation.")
    if used_mock:
        unverified.append("Live web verification is disabled in this run (demo data in use).")

    return verified, unverified


STATUS_LABELS: dict[str, str] = {
    "FRAUDULENT_VERIFIED": "Fraudulent (verified)",
    "LEGITIMATE_VERIFIED": "Legitimate (verified)",
    "LIKELY_LEGITIMATE_UNVERIFIED": "Likely legitimate (unverified)",
    "NEEDS_VERIFICATION": "Needs verification",
    "EXPIRED": "Expired (not necessarily fake)",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence",
    "CONFLICTING_EVIDENCE": "Conflicting evidence",
}


def _summary(
    status: OpportunityStatus,
    level: RiskLevel,
    signals: list[RiskSignal],
    user_input: UserInput,
) -> str:
    name = user_input.company.name or "This opportunity"
    if status == "INSUFFICIENT_EVIDENCE":
        return (
            f"Insufficient evidence: not enough information could be gathered "
            f"about {name} to judge it either way."
        )
    if status == "CONFLICTING_EVIDENCE":
        return (
            f"Conflicting evidence about {name}: part of the investigation "
            f"supports the offer while a serious fraud signal points the other "
            f"way. Verify it directly before acting."
        )
    label = STATUS_LABELS.get(status, status)
    if not signals:
        return f"{label}: no fraud patterns were detected for {name}."
    top = signals[0]
    return (
        f"{label}. {name} triggered {len(signals)} risk signal(s); "
        f"the most serious is: {top.label.lower()}."
    )
