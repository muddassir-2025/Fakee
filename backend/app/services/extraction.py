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
    domain_mentions_company,
    domains_from_urls,
    extract_domain,
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


# A "Company:" value normally ends at the line break, but a posting may put
# several fields on one line (the README's own example). These labels are the
# fields that can follow the name, and the boundary pattern below cuts the
# capture at the first one — or at a sentence end, a hard separator, or an
# amount of money.
COMPANY_FIELD_LABELS = (
    "role", "position", "job title", "designation", "profile", "website",
    "location", "salary", "stipend", "ctc", "package", "eligibility",
    "selection process", "process", "apply", "contact", "email", "phone",
    "qualification", "requirements", "responsibilities", "duration",
    "deadline", "batch", "posted",
)
COMPANY_NAME_BOUNDARY_RE = re.compile(
    r"(?:"
    # a following field label ("Role:", "Apply here:", "Website -")
    r"\b(?:" + "|".join(COMPANY_FIELD_LABELS) + r")\b(?:\s+[A-Za-z]+){0,2}\s*[:\-–]"
    # the end of a sentence — but not the dot of an abbreviation inside a name
    r"|(?<=\w{3})[.!?]\s+"
    r"(?!(?:ltd|limited|inc|incorporated|corp|corporation|llc|pvt|private|co|"
    r"plc|llp|gmbh)\b)(?=[A-Za-z0-9])"
    # a hard separator, or an amount of money ("Pay Rs 1,500", "₹2,000")
    r"|[;|•\t]"
    r"|\b(?:pay|paid|pays|paying|charge|charged|send|sent|transfer|transferred|"
    r"deposit|deposited)?\s*(?:rs\.?|inr|₹)\s*[\d,]+"
    r")",
    re.IGNORECASE,
)
# Abbreviations that keep their dot when a sentence happens to end right after
# them: "Cache Serve Ltd. Please review…" is still "Cache Serve Ltd.".
LEGAL_SUFFIX_TOKENS = {
    "ltd", "limited", "inc", "incorporated", "corp", "corporation", "llc",
    "l.l.c", "co", "pvt", "private", "plc", "llp", "gmbh",
}
# Words that can trail a cut name once its sentence was removed ("ABC
# Technologies asked for a " + "Rs 1,500"). Only stripped *after* a boundary was
# found, so a name that ends in one of them on its own ("Samsung Pay") is safe.
# Words that mean a captured name has run on into the surrounding sentence
# ("eTeam for students interested in Generative AI"). They never belong to an
# employer's name, and the name keys the topic filter, the search queries and the
# stored company record, so the capture is cut at the first one. Deliberately
# excludes "of" and "&": "Bank of Baroda" and "Larsen & Toubro" are names.
NAME_CONTINUATION_RE = re.compile(
    r"\s+(?:for|is|are|was|were|who|that|which|available|offering|offers)\b",
    re.IGNORECASE,
)
# A capture that is only one of these is not a company name at all ("We are
# hiring…" captures "We are"), so a weaker cue is tried instead of storing junk
# that would key every search query.
STOPWORD_NAMES = {
    "a", "an", "and", "dear", "here", "i", "it", "our", "students", "that",
    "the", "there", "they", "this", "we", "you",
}
COMPANY_TRAILING_NOISE = {
    "a", "an", "and", "are", "ask", "asked", "asking", "at", "called",
    "contacted", "for", "from", "i", "in", "is", "it", "me", "my", "of",
    "on", "or", "our", "pay", "paid", "paying", "said", "sent", "then",
    "they", "to", "told", "us", "was", "we", "were", "with",
}

# "Levroxen LLC", "Acme Pvt Ltd", "Foo Inc." at the start of a line/title.
LEGAL_ENTITY_RE = re.compile(
    r"^((?:[A-Z][\w&.'\-]*\s+){0,4}(?:LLC|L\.L\.C|Ltd|Limited|Inc|Incorporated|Corp|Corporation|"
    r"Pvt\.?\s*Ltd\.?|Private\s+Limited))\b",
    re.MULTILINE,
)


# A fee whose own value says it costs nothing is not a fee: "Registration Fee:
# Free", "security deposit: nil", "training fee waived", "registration fee of
# Rs 0". `has_unnegated` only sees a negation *before* the keyword, so without
# this a genuinely free scholarship test was read as demanding an upfront payment
# and came back as a verified-fraud verdict.
FREE_VALUE_RE = re.compile(
    r"^\s*(?:is|of|was)?\s*[:\-–—=]?\s*(?:₹|rs\.?|inr)?\s*(?:completely\s+)?"
    r"(?:free|nil|zero|waived(?:\s+off)?|not\s+required|not\s+applicable|n/?a|0(?:\.0+)?)\b",
    re.IGNORECASE,
)


def _mentions(text: str, keyword: str) -> bool:
    """Whole-word mention of a money keyword.

    Substring matching made the query string `?feedback=6` look like a fee, which
    turned a free scholarship test into a demand for payment.
    """
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE) is not None


def _states_free(text: str, keyword: str) -> bool:
    """True when *every* mention of this money keyword states it costs nothing."""
    occurrences = list(
        re.finditer(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE)
    )
    if not occurrences:
        return False
    return all(
        FREE_VALUE_RE.match(text[m.end() : m.end() + 32]) for m in occurrences
    )


# A domain introduced as a *partner* is neither an application link nor the
# employer's website: "Mue AI Pvt. Ltd., in collaboration with v4c.ai" made the
# partner's site look like a mismatched employer domain.
PARTNER_CONTEXT_RE = re.compile(
    r"(?:in\s+collaboration\s+with|in\s+association\s+with|in\s+partnership\s+with|"
    r"collaboration\s+with|partner(?:ed|ing)?\s+with|powered\s+by)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)


def _actionable_urls(text: str, urls: list[str]) -> list[str]:
    """The URLs a candidate is actually meant to act on."""
    kept: list[str] = []
    for url in urls:
        index = text.find(url)
        before = text[max(0, index - 40) : index] if index != -1 else ""
        if PARTNER_CONTEXT_RE.search(before):
            continue
        kept.append(url)
    return kept


def _trim_company_name(value: str) -> str:
    """Cut a captured company value at the first field or sentence boundary.

    The capture after "Company:" used to run to the end of the line, so a
    posting with several fields on one line produced the whole sentence as the
    company name ("ABC Technologies. Selected on WhatsApp without interview…").
    That name then keyed the topic filter, the search queries and the stored
    company record, so it has to be the name and nothing else.
    """
    match = COMPANY_NAME_BOUNDARY_RE.search(value)
    continuation = NAME_CONTINUATION_RE.search(value)
    if continuation and (match is None or continuation.start() < match.start()):
        match = continuation
    if not match:
        return value.strip(" \t-–—,;:|")

    head = value[: match.start()]
    words = head.split()
    # Keep the dot of a legal suffix the sentence ended on.
    if value.startswith(".", match.start()) and words and words[-1].lower() in LEGAL_SUFFIX_TOKENS:
        head += "."
        words = head.split()

    while words and words[-1].lower().strip(".,;:!?'\"()") in COMPANY_TRAILING_NOISE:
        words.pop()
    return " ".join(words).strip(" \t-–—,;:|")


def _usable_company_name(name: str | None) -> bool:
    """Reject a capture that cannot be a company name (empty, too short, pronoun)."""
    value = (name or "").strip()
    if len(value) < 2:
        return False
    return value.lower().strip(".,") not in STOPWORD_NAMES


def _find_company_name(text: str) -> str | None:
    match = COMPANY_LINE_RE.search(text)
    if match:
        name = _trim_company_name(match.group(1).strip().splitlines()[0])[:120]
        if _usable_company_name(name):
            return name

    # A legal-entity suffix on the first line is a strong company-name cue
    # (e.g. "Levroxen LLC - Online Assessment Registration").
    legal = LEGAL_ENTITY_RE.search(text)
    if legal:
        return legal.group(1).strip()[:120]

    # "HCLTech Hiring", "ADP – GPT Intern", "Company - Feuji"
    #
    # Each cue carries its own flags: `[A-Z]` is only meaningful case-sensitively.
    # Compiled with IGNORECASE it matched ordinary words, and
    # "…role with an international client" stored "an international client" as the
    # employer — which then keyed every search query.
    for pattern, flags in (
        (r"^([A-Z][\w&.,' -]{2,40})\s+(?i:hiring|is hiring|recruitment)", re.MULTILINE),
        # "…available with eTeam for students…": only the capitalised tokens are
        # the name, so the capture stops at the first lowercase word instead of
        # swallowing the rest of the sentence (up to four tokens, for
        # "ABC Technologies Pvt Ltd").
        (r"\b(?i:with)\s+((?:[A-Z][\w&.'\-]*)(?:\s+[A-Z][\w&.'\-]*){0,3})", 0),
        # "QuickHyre AI is inviting applications", "WeMakeScholars is organizing"
        (
            r"\b((?:[A-Z][\w&.'\-]*)(?:\s+[A-Z][\w&.'\-]*){0,3})\s+"
            r"(?i:is)\s+(?i:inviting|organi[sz]ing|conducting|offering)\b",
            0,
        ),
        (r"(?i:Company)\s*[-–:]\s*([^\n]+)", 0),
    ):
        m = re.search(pattern, text, flags)
        if m:
            name = _trim_company_name(m.group(1).strip())[:120]
            if _usable_company_name(name):
                return name
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
    urls = _actionable_urls(text, extract_urls(text))
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
    # Negation-aware: "No registration fee" must NOT be read as a fee request,
    # and neither must "Registration Fee: Free" (see `_states_free`).
    for kw in MONEY_KEYWORDS:
        if not _mentions(text, kw):
            continue
        if not has_unnegated(lower, [kw]):
            continue
        if _states_free(text, kw):
            continue
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

    company_name = _find_company_name(text)
    # The employer's own site, not just the first URL in the text. When no link
    # plausibly belongs to the named employer, the website stays unknown — which
    # is the honest answer, and stops a partner's domain being scored as a
    # mismatch. Without a company name the domain is the only subject there is.
    website = next(
        (
            url
            for url in urls
            if company_name and domain_mentions_company(extract_domain(url), company_name)
        ),
        None if company_name else (urls[0] if urls else None),
    )

    return UserInput(
        company=CompanyInfo(
            name=company_name,
            website=website,
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
