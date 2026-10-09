"""Positive-side (trust / evidence) scoring.

Risk patterns record what is *wrong* with a posting. This module records what is
*right*, so a genuine and well-evidenced opportunity is not punished merely for
using WhatsApp, a short link or a Google Form — signals that appear in real
postings too. The trust score is kept separate from the risk score, exactly as
the risk verdict is kept separate from the opportunity status.
"""

from __future__ import annotations

from ..schemas import DomainIntel, ReviewSignals, TrustSignal, UserInput
from .text_utils import extract_domain, is_free_email, normalize_name

# Identity / financial credentials that should never be requested.
SENSITIVE_TERMS = (
    "otp", "cvv", "pin number", "upi pin", "card number", "bank account",
    "net banking", "internet banking", "aadhaar number", "aadhar number",
    "pan card number", "password",
)
SELECTION_STAGE_TERMS = (
    "online assessment", "online test", "written test", "technical interview",
    "hr interview", "hr discussion", "technical round", "aptitude",
    "group discussion", "selection process",
)
ELIGIBILITY_TERMS = (
    "eligibility", "batch", "pass-out", "passout", "cgpa", "percentage",
    "graduating", "final year", "3rd year", "4th year",
)


def _company_domains(user_input: UserInput) -> set[str]:
    """Domains that plausibly belong to the named employer."""
    domains: set[str] = set()
    candidates = list(user_input.contacts.domains)
    website = extract_domain(user_input.company.website)
    if website:
        candidates.append(website)
    for domain in candidates:
        if not domain:
            continue
        domain = domain.lower()
        domains.add(domain)
        parts = domain.split(".")
        if len(parts) > 2:  # also the registrable domain (jobs.adp.com -> adp.com)
            domains.add(".".join(parts[-2:]))
    return domains


def _application_domain(user_input: UserInput) -> str | None:
    for url in user_input.contacts.urls:
        domain = extract_domain(url)
        if domain:
            return domain
    return user_input.contacts.domains[0] if user_input.contacts.domains else None


def _domain_mentions_company(domain: str | None, name: str | None) -> bool:
    if not domain or not name:
        return False
    normalized = normalize_name(name)
    if not normalized:
        return False
    label = domain.lower().split(".")[0]
    return normalized[:6] in normalize_name(label) or normalize_name(label) in normalized


def assess_trust(
    user_input: UserInput,
    *,
    domain: DomainIntel | None = None,
    reviews: ReviewSignals | None = None,
    sources_checked: int = 0,
) -> tuple[int, list[TrustSignal]]:
    """Return (trust_score 0..100, positive signals)."""
    text = (user_input.raw_text or "").lower()
    signals: list[TrustSignal] = []

    def add(signal_id: str, label: str, points: int, explanation: str) -> None:
        signals.append(
            TrustSignal(id=signal_id, label=label, points=points, explanation=explanation)
        )

    if not user_input.money_request.detected:
        add(
            "no_money_requested",
            "No fee requested",
            +10,
            "The posting does not ask the candidate to pay any money.",
        )

    if not any(term in text for term in SENSITIVE_TERMS):
        add(
            "no_sensitive_data_requested",
            "No sensitive data requested",
            +15,
            "No OTP, PIN, card or banking credentials are requested.",
        )

    company_domains = _company_domains(user_input)
    name = user_input.company.name

    corporate_email = None
    for email in user_input.contacts.emails:
        email_domain = email.split("@")[-1]
        if is_free_email(email_domain):
            continue
        if email_domain in company_domains or _domain_mentions_company(email_domain, name):
            corporate_email = email_domain
            break
    if corporate_email:
        add(
            "corporate_email_matches_employer",
            "Contact uses the employer's domain",
            +25,
            f"The listed contact address is on the employer's own domain ({corporate_email}).",
        )

    application_domain = _application_domain(user_input)
    domain_matches = bool(
        (domain and domain.company_name_match)
        or (application_domain and application_domain in company_domains)
        or _domain_mentions_company(application_domain, name)
    )
    if domain_matches:
        add(
            "application_domain_matches_employer",
            "Application domain matches the employer",
            +20,
            "The application link points at the employer's own domain.",
        )

    official_source = bool(domain and domain.company_name_match and domain.dns_resolves is not False)
    if official_source:
        add(
            "official_source_found",
            "Official website found",
            +25,
            "A live domain matching the named employer was located.",
        )

    stages = [t for t in SELECTION_STAGE_TERMS if t in text]
    if user_input.opportunity.selection_process or len(stages) >= 2:
        add(
            "detailed_selection_process",
            "Detailed, plausible selection process",
            +15,
            "The posting describes a concrete, multi-stage selection process.",
        )

    if sources_checked > 0 and reviews is not None and reviews.negative_mentions == 0:
        add(
            "no_negative_reputation",
            "No negative reports found",
            +20,
            f"{sources_checked} web source(s) examined with no negative reports.",
        )

    eligibility = [t for t in ELIGIBILITY_TERMS if t in text]
    if user_input.opportunity.title and eligibility:
        add(
            "clear_eligibility",
            "Clear role and eligibility",
            +10,
            "The role and eligibility criteria are stated concretely.",
        )

    score = min(100, sum(s.points for s in signals))
    return score, signals
