"""Stage 1 — normalize the user's messy text into JSON 1 (``UserInput``).

Primary path: Groq. Fallback: deterministic heuristics, so the pipeline always
produces a usable object even without an API key.
"""

from __future__ import annotations

import logging
import re

from ..config import settings
from ..schemas import (
    CompanyInfo,
    CommunicationChannels,
    ContactPoints,
    MoneyRequest,
    OpportunityInfo,
    UserInput,
)
from .groq_client import groq_client
from .text_utils import (
    domains_from_urls,
    extract_emails,
    extract_phones,
    extract_urls,
    has_unnegated,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a recruitment-fraud analyst. You receive raw, messy text a \
user pasted about a job, internship, or company. Normalize it into strict JSON.

Return ONLY a JSON object with this exact shape:
{
  "company": {"name": string|null, "website": string|null, "claimed_location": string|null},
  "opportunity": {"type": "job"|"internship"|"unknown", "title": string|null, "salary": string|null,
                   "location": string|null, "duration": string|null, "selection_process": string|null},
  "claims": [string],                       // notable claims made to the user, verbatim-ish
  "communication": {"whatsapp": bool, "telegram": bool, "email": bool, "phone": bool,
                     "instagram": bool, "youtube": bool, "linkedin": bool},
  "money_request": {"detected": bool, "amount": string|null, "reason": string|null}
}

Rules:
- Never invent facts. Use null when unknown.
- "claims" should capture statements like selection without interview, guaranteed
  placement, free laptop/phone/reward, urgency, or fees. Only include claims
  actually present in the text.
- money_request.detected is true only if the text asks the candidate to pay.
- Output JSON only. No markdown, no commentary."""

COMPANY_LINE_RE = re.compile(
    r"(?:company|organisation|organization|firm|employer)\s*[:\-–]\s*(.+)", re.IGNORECASE
)
ROLE_LINE_RE = re.compile(
    r"(?:role|position|job title|profile|designation)\s*[:\-–]\s*(.+)", re.IGNORECASE
)
SALARY_RE = re.compile(
    r"(?:₹|rs\.?|inr)\s?[\d,.]+(?:\s?[-–]\s?(?:₹|rs\.?|inr)?\s?[\d,.]+)?"
    r"(?:\s?(?:lpa|lakhs?|l|k|per month|/month|pm|monthly))?",
    re.IGNORECASE,
)
LPA_RE = re.compile(r"[\d.]+\s*(?:lpa|lakhs?\s+per\s+annum)", re.IGNORECASE)
MONEY_KEYWORDS = (
    "registration fee", "application fee", "training fee", "security deposit",
    "processing fee", "certificate fee", "equipment fee", "refundable deposit",
    "pay ", "payment", "fee", "deposit", "gift card", "crypto", "upi",
)
CLAIM_KEYWORDS = (
    "without interview", "no interview", "selected", "guaranteed", "free laptop",
    "free iphone", "free phone", "free tablet", "reward", "direct joining",
    "no experience", "instant", "urgent", "limited seats", "first come",
)


# "Levroxen LLC", "Acme Pvt Ltd", "Foo Inc." at the start of a line/title.
LEGAL_ENTITY_RE = re.compile(
    r"^((?:[A-Z][\w&.'\-]*\s+){0,4}(?:LLC|L\.L\.C|Ltd|Limited|Inc|Incorporated|Corp|Corporation|"
    r"Pvt\.?\s*Ltd\.?|Private\s+Limited))\b",
    re.MULTILINE,
)


def _find_company_name(text: str) -> str | None:
    match = COMPANY_LINE_RE.search(text)
    if match:
        return match.group(1).strip().splitlines()[0][:120]

    # A legal-entity suffix on the first line is a strong company-name cue
    # (e.g. "Levroxen LLC - Online Assessment Registration").
    legal = LEGAL_ENTITY_RE.search(text)
    if legal:
        return legal.group(1).strip()[:120]

    # "HCLTech Hiring", "ADP – GPT Intern", "Company - Feuji"
    for pattern in (
        r"^([A-Z][\w&.,' -]{2,40})\s+(?:hiring|is hiring|recruitment)",
        r"\bwith\s+([A-Z][\w&.,' -]{2,40})",
        r"Company\s*[-–:]\s*([^\n]+)",
    ):
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()[:120]
    return None


def _find_role(text: str) -> str | None:
    inline = re.search(
        r"\b(?:role|position)\s*[-:–]\s*([^\n]+)", text, re.IGNORECASE
    )
    if inline:
        return inline.group(1).strip()[:160]
    match = ROLE_LINE_RE.search(text)
    if match:
        return match.group(1).strip()[:160]
    m = re.search(r"\b([A-Za-z0-9/&+ ]{2,40}(?:Intern|Engineer|Developer|Analyst|Associate|Trainee|Manager))\b", text)
    if m:
        return m.group(1).strip()[:160]
    return None


def _find_salary(text: str) -> str | None:
    m = SALARY_RE.search(text)
    if m:
        return m.group(0).strip()
    m = LPA_RE.search(text)
    if m:
        return m.group(0).strip()
    m = re.search(r"(?:stipend|salary|package|ctc)\s*[-:–]?\s*([^\n]{2,60})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:80]
    return None


def _find_location(text: str) -> str | None:
    m = re.search(r"(?:location|venue|city)\s*[-:–]\s*([^\n]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:120]
    return None


def heuristic_extract(text: str) -> UserInput:
    """Deterministic fallback extraction — no external calls."""
    lower = text.lower()
    urls = extract_urls(text)
    emails = extract_emails(text)
    phones = extract_phones(text)
    domains = domains_from_urls(urls)

    communication = CommunicationChannels(
        whatsapp="whatsapp" in lower or "wa.me" in lower,
        telegram="telegram" in lower or "t.me" in lower,
        email=bool(emails) or "@" in text,
        phone=bool(phones),
        instagram="instagram" in lower,
        youtube="youtube" in lower or "youtu.be" in lower,
        linkedin="linkedin" in lower or "lnkd.in" in lower,
    )

    money_amount: str | None = None
    money_reason: str | None = None
    money_detected = False
    # Negation-aware: "No registration fee" must NOT be read as a fee request.
    for kw in MONEY_KEYWORDS:
        if has_unnegated(lower, [kw]):
            money_detected = True
            money_reason = kw.strip()
            break
    amount_match = re.search(
        r"(?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?", text, re.IGNORECASE
    )
    if amount_match and money_detected:
        money_amount = amount_match.group(0).strip()

    claims: list[str] = []
    for sentence in re.split(r"[\n.!]", text):
        s = sentence.strip()
        if not s:
            continue
        low = s.lower()
        if any(kw in low for kw in CLAIM_KEYWORDS):
            claims.append(s[:200])
    # De-duplicate while preserving order.
    claims = list(dict.fromkeys(claims))[:12]

    opp_type = "internship" if "intern" in lower else ("job" if "job" in lower or "hiring" in lower else "unknown")

    return UserInput(
        company=CompanyInfo(
            name=_find_company_name(text),
            website=urls[0] if urls else None,
            claimed_location=_find_location(text),
        ),
        opportunity=OpportunityInfo(
            type=opp_type,
            title=_find_role(text),
            salary=_find_salary(text),
            location=_find_location(text),
        ),
        claims=claims,
        communication=communication,
        money_request=MoneyRequest(
            detected=money_detected, amount=money_amount, reason=money_reason
        ),
        contacts=ContactPoints(
            emails=emails,
            phones=phones,
            urls=urls,
            domains=domains,
        ),
        raw_text=text,
        extraction_source="heuristic",
    )


def heuristic_is_reliable(user_input: UserInput) -> bool:
    """Whether the deterministic extraction is good enough to skip Groq.

    The heuristics are reliable when they already identify *who* the posting is
    about and at least one concrete signal (money, a contact, a channel, or a
    notable claim). Groq is reserved for the messy/ambiguous cases it actually
    improves, which saves a whole request per investigation.
    """
    has_subject = bool(user_input.company.name) or bool(user_input.contacts.domains)
    has_signal = bool(
        user_input.money_request.detected
        or user_input.contacts.domains
        or user_input.contacts.emails
        or user_input.communication.whatsapp
        or user_input.communication.telegram
        or user_input.claims
    )
    return has_subject and has_signal


async def extract_user_input(text: str) -> UserInput:
    """Return JSON 1, preferring Groq and falling back to heuristics."""
    fallback = heuristic_extract(text)

    skip_groq = settings.prefer_heuristic_extraction and heuristic_is_reliable(fallback)
    if groq_client.enabled and not skip_groq:
        parsed = await groq_client.chat_json(
            SYSTEM_PROMPT, f"RAW USER TEXT:\n\"\"\"\n{text[:8000]}\n\"\"\""
        )
        if isinstance(parsed, dict):
            try:
                merged = _merge_with_fallback(parsed, fallback, text)
                merged.extraction_source = "groq"
                return merged
            except Exception as exc:  # noqa: BLE001
                logger.warning("Groq extraction merge failed: %s", exc)

    return fallback


def _merge_with_fallback(parsed: dict, fallback: UserInput, text: str) -> UserInput:
    """Trust Groq for semantics, but never lose deterministically-found contacts."""
    company = {**fallback.company.model_dump(), **(parsed.get("company") or {})}
    opportunity = {**fallback.opportunity.model_dump(), **(parsed.get("opportunity") or {})}
    communication = {
        **fallback.communication.model_dump(),
        **(parsed.get("communication") or {}),
    }
    money = {**fallback.money_request.model_dump(), **(parsed.get("money_request") or {})}
    claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else fallback.claims

    return UserInput(
        company=CompanyInfo(**company),
        opportunity=OpportunityInfo(**opportunity),
        claims=[str(c)[:200] for c in claims][:20],
        communication=CommunicationChannels(**communication),
        money_request=MoneyRequest(**money),
        contacts=fallback.contacts,
        raw_text=text,
    )
