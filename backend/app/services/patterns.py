"""Stage 6 — the scam-pattern engine.

Deterministic, rule-based detection over four sources of signal:

* the structured user input (what the "recruiter" actually offered/asked),
* domain intelligence,
* aggregated web review signals,
* historical reports stored in the intelligence database.

Detecting *patterns of signals* rather than single keywords is the core idea.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..schemas import DetectedPattern, DomainIntel, ReviewSignals, UserInput

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .exa import SearchResult
from .text_utils import domain_mentions_company, extract_domain, has_unnegated

# --------------------------------------------------------------------------
# Pattern catalog
# --------------------------------------------------------------------------
PATTERN_CATALOG: dict[str, dict[str, str]] = {
    "upfront_payment": {
        "label": "Upfront payment requested",
        "category": "money",
        "severity": "critical",
        "description": "The candidate is asked to pay a fee/deposit to get or keep the role.",
    },
    "suspicious_reward_offer": {
        "label": "Suspicious free reward offer",
        "category": "money",
        "severity": "high",
        "description": "Free laptop/phone/reward used as bait, often tied to a payment.",
    },
    "untraceable_payment": {
        "label": "Untraceable payment method",
        "category": "money",
        "severity": "critical",
        "description": "Payment requested via gift card, crypto, or wallet with no recourse.",
    },
    "fake_check_payment": {
        "label": "Fake check / repayment demand",
        "category": "money",
        "severity": "critical",
        "description": "A cheque is 'sent' and the candidate is told to forward money or buy gift cards.",
    },
    "whatsapp_recruitment": {
        "label": "Recruitment via WhatsApp",
        "category": "channel",
        "severity": "low",
        "description": "Talent acquisition runs through WhatsApp instead of formal channels.",
    },
    "telegram_recruitment": {
        "label": "Recruitment via Telegram",
        "category": "channel",
        "severity": "low",
        "description": "Talent acquisition runs through Telegram groups/channels.",
    },
    "social_only_recruitment": {
        "label": "No formal application channel",
        "category": "channel",
        "severity": "low",
        "description": "Contact relies on social/messaging rather than an official careers portal.",
    },
    "shortener_link": {
        "label": "Shortened application link",
        "category": "channel",
        "severity": "medium",
        "description": "Application URL is obfuscated behind a link shortener.",
    },
    "unofficial_application_channel": {
        "label": "Application link is not the employer's domain",
        "category": "channel",
        "severity": "medium",
        "description": (
            "The posting names an employer but routes registration through a link on a "
            "domain that employer does not own, so the destination cannot be verified."
        ),
    },
    "free_email_contact": {
        "label": "Contact via free email provider",
        "category": "channel",
        "severity": "low",
        "description": "Official recruitment uses a personal/free mailbox rather than a company domain.",
    },
    "sensitive_data_request": {
        "label": "Sensitive data requested",
        "category": "personal_data",
        "severity": "critical",
        "description": "Banking details, OTP, CVV, passwords or govt ID requested during recruitment.",
    },
    "document_request_pre_offer": {
        "label": "Documents requested before an offer",
        "category": "personal_data",
        "severity": "low",
        "description": "Identity documents requested unusually early in the process.",
    },
    "unusually_high_salary": {
        "label": "Unusually high pay for the role",
        "category": "opportunity",
        "severity": "high",
        "description": "Compensation is far above market for the stated experience level.",
    },
    "no_interview_selection": {
        "label": "Selected without an interview",
        "category": "opportunity",
        "severity": "high",
        "description": "Selection happens with no meaningful assessment or interview.",
    },
    "guaranteed_selection": {
        "label": "Guaranteed selection / placement",
        "category": "opportunity",
        "severity": "high",
        "description": "Outcome is guaranteed regardless of merit — a classic lure.",
    },
    "urgency_pressure": {
        "label": "High-pressure urgency",
        "category": "opportunity",
        "severity": "medium",
        "description": "Deadlines in hours, 'limited seats', or pay-today framing.",
    },
    "unrealistic_easy_money": {
        "label": "High pay for minimal effort",
        "category": "opportunity",
        "severity": "high",
        "description": "Very high daily pay is promised for trivial, effort-free work.",
    },
    "reshipping_scheme": {
        "label": "Package reshipping / forwarding",
        "category": "opportunity",
        "severity": "critical",
        "description": "The role is to receive and forward packages — a known money-mule scheme.",
    },
    "recent_domain": {
        "label": "Recently registered domain",
        "category": "domain",
        "severity": "high",
        "description": "Website was registered very recently, inconsistent with an established employer.",
    },
    "domain_name_mismatch": {
        "label": "Domain does not match company name",
        "category": "domain",
        "severity": "high",
        "description": "The web address is unrelated to the company it claims to represent.",
    },
    "cheap_tld": {
        "label": "Low-cost domain extension",
        "category": "domain",
        "severity": "low",
        "description": "Uses a throwaway TLD often seen in short-lived recruitment scams (weak signal).",
    },
    "domain_unreachable": {
        "label": "Website does not resolve",
        "category": "domain",
        "severity": "medium",
        "description": "Claimed domain does not resolve over DNS or is unreachable.",
    },
    "no_company_footprint": {
        "label": "Almost no verifiable company footprint",
        "category": "reputation",
        "severity": "high",
        "description": "Little or no independent evidence the company exists as described.",
    },
    "fraud_accusations_against_company": {
        "label": "Independent fraud accusations",
        "category": "reputation",
        "severity": "high",
        "description": (
            "Independent third-party sources accuse the company itself of defrauding "
            "or cheating candidates (not workplace dissatisfaction, and not the "
            "company being impersonated)."
        ),
    },
    "claim_contradicted_by_official_source": {
        "label": "An official source contradicts the posting",
        "category": "opportunity",
        "severity": "high",
        "description": (
            "An official or authoritative source states different terms for this "
            "programme than the posting does — pay, eligibility, or the channel it "
            "must be applied through."
        ),
    },
    "negative_reputation": {
        "label": "Negative reports found online",
        "category": "reputation",
        "severity": "medium",
        "description": "Web sources describe negative candidate experiences with this company.",
    },
    "repeated_payment_complaints": {
        "label": "Multiple reports of payment demands",
        "category": "reputation",
        "severity": "critical",
        "description": "Several independent sources describe fee demands from candidates.",
    },
    "repeated_channel_complaints": {
        "label": "Multiple reports of messaging-app recruitment",
        "category": "reputation",
        "severity": "low",
        "description": "Several sources describe recruitment via WhatsApp/Telegram.",
    },
    "security_checker_flag": {
        "label": "Flagged by a website-safety checker",
        "category": "reputation",
        "severity": "high",
        "description": (
            "An independent checker that scores domains for fraud/phishing reports "
            "places this site in its flagged band, or states outright that it looks "
            "like a scam."
        ),
    },
    "offer_tied_to_paid_service": {
        "label": "Benefit conditional on using their service",
        "category": "opportunity",
        "severity": "medium",
        "description": (
            "The posted benefit (scholarship, waiver, reward) is available only to "
            "people who buy through the sender — a sales route dressed as a merit "
            "award, not a fraud in itself."
        ),
    },
    "historical_reports": {
        "label": "Previously reported to this platform",
        "category": "history",
        "severity": "high",
        "description": "The company/opportunity has prior reports in our intelligence database.",
    },
}


def _p(
    pattern: str,
    detected: bool,
    confidence: float,
    rationale: str,
    evidence: list[str] | None = None,
    severity: str | None = None,
) -> DetectedPattern | None:
    if not detected:
        return None
    meta = PATTERN_CATALOG[pattern]
    return DetectedPattern(
        pattern=pattern,
        category=meta["category"],
        detected=True,
        severity=severity or meta["severity"],  # type: ignore[arg-type]
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        rationale=rationale,
        evidence=evidence or [],
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
MONEY_REASONS = {
    "registration fee", "application fee", "training fee", "security deposit",
    "processing fee", "certificate fee", "equipment fee", "joining fee",
    "refundable deposit", "documentation fee", "verification fee",
}
REWARD_TERMS = {
    "free laptop", "free iphone", "free phone", "free tablet", "free mobile",
    "free gift", "free reward", "free bike", "free car", "welcome kit",
}
UNTRACEABLE_TERMS = {"gift card", "crypto", "bitcoin", "usdt", "wallet", "amazon pay voucher"}
SENSITIVE_TERMS = {
    "otp", "cvv", "card number", "bank account", "net banking", "internet banking",
    "password", "aadhaar number", "aadhar number", "pan card number", "upi pin", "pin number",
}
# Credentials only count when the posting *asks for* them. "Create a strong
# password" on a signup page is normal and must not be read as a data request.
SENSITIVE_REQUEST_VERBS = (
    "send", "share", "provide", "submit", "give", "reply with", "upload",
    "enter", "tell us", "confirm your", "verify your", "forward", "mention",
)


def _sensitive_hits(blob: str) -> list[str]:
    hits: list[str] = []
    for term in SENSITIVE_TERMS:
        start = 0
        while True:
            idx = blob.find(term, start)
            if idx == -1:
                break
            if any(verb in blob[max(0, idx - 80) : idx] for verb in SENSITIVE_REQUEST_VERBS):
                hits.append(term)
                break
            start = idx + len(term)
    return hits
# Fake-check / reshipping money-flow patterns (FTC-documented).
FAKE_CHECK_PATTERNS = [
    re.compile(r"deposit\s+the\s+(?:cheque|check)"),
    re.compile(r"(?:send|transfer|forward|wire)\s+the\s+remaining"),
    re.compile(r"(?:buy|purchase|send)\s+gift\s?cards?"),
    re.compile(r"deposit\s+(?:it|this|the)\b[^.\n]{0,30}\b(?:keep|send|transfer)"),
]
# Patterns that, on their own, are conclusive evidence of fraud: the posting
# itself instructs the candidate to pay, hand over credentials, or launder a
# fake cheque. These drive the FRAUDULENT_VERIFIED status.
CONCLUSIVE_FRAUD_PATTERNS = frozenset(
    {"upfront_payment", "untraceable_payment", "sensitive_data_request", "fake_check_payment"}
)
URGENCY_TERMS = {
    "limited seats", "first come", "immediately", "within 24 hours", "today only",
    "urgent hiring", "apply now", "last date today", "48 hours",
}
# "Earn ₹X per day" combined with "no skills/no interview" — the easy-money bait
# used by task/CAPTCHA/data-entry scams.
EASY_MONEY_RE = re.compile(
    r"(?:earn|income|salary|payment|payout)[^.\n]{0,30}(?:per\s+day|daily|/day)"
)
NO_EFFORT_TERMS = (
    "no skills", "no skill needed", "no experience needed", "minimal effort",
    "easy money", "30 minutes a day", "few hours a day", "simple work",
    "no interview",
)
# Package reshipping / money-mule scheme (FTC-documented).
RESHIPPING_TERMS = (
    "repack", "reshipping", "parcel forwarding", "receive packages",
    "receive the packages", "forward the package", "forward packages",
    "shipping labels", "parcel forwarding executive",
)
NO_INTERVIEW_TERMS = {"without interview", "no interview", "direct joining", "no technical"}
GUARANTEE_TERMS = {"guaranteed", "100% placement", "assured", "confirmed selection"}

# --------------------------------------------------------------- checkers ---
# Independent website-safety checkers publish a verdict per domain. Their pages
# are worth reading but easy to mis-read: almost every title contains the word
# "scam" ("… Reviews | scam, legit or safe check"), so matching the word would
# flag every company that has ever been listed. Only the *verdict* counts — an
# explicit scam/phishing finding, or a trust score in the bottom band.
CHECKER_DOMAINS = frozenset(
    {
        "scamadviser.com", "scam-detector.com", "gridinsoft.com", "urlvoid.com",
        "scamvoid.com", "webutation.net", "sitejabber.com", "trustpilot.com",
    }
)
# Scamadviser's 48/100 reads as "average to good", so the cut-off has to sit in
# the checkers' own "likely a scam" band or a merely mediocre site gets a high
# severity fraud signal.
CHECKER_FLAG_SCORE = 30
CHECKER_VERDICT_RE = re.compile(
    r"(?:might|may|appears?\s+to)\s+be\s+a\s+scam"
    r"|\b(?:likely|probably|definitely)\s+a\s+scam"
    r"|\b(?:is|was)\s+an?\s+(?:scam|fraudulent\s+site)"
    r"|\bphishing\b"
    # Scam Detector's own wording for a negative finding: "we raised the alert on
    # <domain>". A positive page says "the connection is valid" instead.
    r"|\b(?:raised|raise[sd]?)\s+(?:the\s+|an?\s+)?alert\b"
    r"|\b(?:is|was|has\s+been)\s+flagged\b"
    r"|\bhigh[\s-]risk\s+(?:website|domain|financial)"
    r"|\bnot\s+safe\s+to\s+(?:use|buy|shop|visit)",
    re.IGNORECASE,
)
# Independent pages that call the company itself a scam. A question ("Is X a
# scam?") is not an accusation, so the match is rejected when it is followed by
# a question mark; checkers and the company's own site are excluded, because both
# have their own verdict.
ACCUSATION_RE = re.compile(
    r"\b(?:is|was|are|were)\s+(?:a\s+)?(?:a\s+)?(?:big\s+|total\s+|complete\s+|pure\s+|huge\s+)?"
    r"(?:scam|fraud|fraudulent)\b"
    r"|\b(?:a|another)\s+(?:big\s+|total\s+|complete\s+|pure\s+)?scam\b"
    r"|\bscam(?:med|ming)?\s+(?:students|candidates|people|applicants|me|us)\b"
    r"|\b(?:cheated|defrauded)\s+(?:students|candidates|people|applicants|me|us)\b",
    re.IGNORECASE,
)
CHECKER_SCORE_RE = re.compile(r"trust\s*(?:score|rating)[^.\n\d]{0,20}(\d{1,3})", re.IGNORECASE)

# A benefit that is conditional on buying through the sender. The posting
# advertises an award; the page that describes its terms says it is only for
# customers of the sender's own service — a sales funnel, which is legitimate to
# point out but not proof of fraud, so it carries a medium weight.
BENEFIT_TERMS = (
    "scholarship", "discount", "waiver", "cashback", "reward", "prize",
    "benefit", "eligib",
)
SERVICE_TERMS = (
    "loan", "subscription", "insurance", "booking", "purchase", "membership",
    "plan", "course", "policy", "account", "service",
)
OFFER_TIE_RES = (
    re.compile(r"only\s+[^.\n]{0,80}\b(?:via|through|with|using)\b", re.IGNORECASE),
    re.compile(r"eligible\s+only\s+(?:if|when|for)\b[^.\n]{0,80}", re.IGNORECASE),
    re.compile(
        r"\b(?:mandatory|required|compulsory)\b[^.\n]{0,40}"
        r"\b(?:take|apply|process|book|purchase|subscribe|pay)\b[^.\n]{0,60}"
        r"\b(?:via|through|with|using)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:must|only\s+those\s+who)\b[^.\n]{0,40}"
        r"\b(?:take|applied|apply|process|book|purchase|subscribe)\b[^.\n]{0,60}"
        r"\b(?:via|through|with|using)\b",
        re.IGNORECASE,
    ),
)


def _blob(user_input: UserInput) -> str:
    parts = [
        user_input.raw_text,
        " ".join(user_input.claims),
        user_input.opportunity.title or "",
        user_input.opportunity.salary or "",
        user_input.money_request.reason or "",
    ]
    return " ".join(parts).lower()


def parse_monthly_amount(salary: str | None) -> tuple[float | None, str | None]:
    """Return (amount_per_month, unit) parsed from a free-text salary string."""
    if not salary:
        return None, None
    text = salary.lower()
    numbers = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+(?:\.\d+)?", salary) if n.strip(",.")]
    if not numbers:
        return None, None
    # An advertised *band* ("Rs 8,300 - Rs 58,275 per month") is an experience
    # range, not a promise of the top figure. Judging "unusually high pay" on the
    # upper bound flagged genuine postings; the midpoint represents the offer.
    amount = (min(numbers) + max(numbers)) / 2 if len(numbers) > 1 else numbers[0]

    if "lpa" in text or "lakh per annum" in text or "per annum" in text:
        return amount * 100000 / 12, "annual"
    if "lakh" in text and "month" not in text:
        return amount * 100000 / 12, "annual"
    if re.search(r"per month|/month|monthly|pm\b|p\.m\.", text):
        return amount, "month"
    if amount >= 100000:  # plainly annual
        return amount / 12, "annual"
    return amount, "month"


def is_unusually_high(user_input: UserInput) -> tuple[bool, str]:
    amount, unit = parse_monthly_amount(user_input.opportunity.salary)
    if amount is None:
        return False, ""
    kind = (user_input.opportunity.type or "unknown").lower()
    threshold = 50000 if kind == "internship" else 90000
    if amount >= threshold:
        return True, (
            f"Stated pay of {user_input.opportunity.salary} is far above typical "
            f"{kind if kind != 'unknown' else 'early-career'} compensation."
        )
    return False, ""


def _page_text(result: SearchResult) -> str:
    return f"{result.title or ''}. {result.snippet or ''}"


def _is_checker(domain: str) -> bool:
    return any(domain == c or domain.endswith("." + c) for c in CHECKER_DOMAINS)


def _company_key(value: str) -> str:
    """Letters only, lowercased: "WeMakeScholars" ≈ "We make scholar".

    Complaint threads spell the name the way the poster did, so an exact string
    match misses the page that matters most ("We make scholar - a big scam").
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _mentions_company(text: str, company: str) -> bool:
    key = _company_key(company)
    return not key or key in _company_key(text)


def _checker_flags(pages: list[SearchResult] | None) -> list[str]:
    """What independent website-safety checkers said about the domain."""
    hits: list[str] = []
    for result in pages or []:
        domain = extract_domain(result.url)
        if not _is_checker(domain):
            continue
        text = _page_text(result)
        verdict = CHECKER_VERDICT_RE.search(text)
        if verdict:
            # "very likely not a scam but legit" is the checker clearing the site.
            if "not " in text[max(0, verdict.start() - 12) : verdict.start()].lower():
                continue
            hits.append(f"{domain}: “{verdict.group(0)}”")
            continue
        for score in CHECKER_SCORE_RE.findall(text):
            if int(score) <= CHECKER_FLAG_SCORE:
                hits.append(f"{domain}: trust score {score}/100")
                break
        if len(hits) >= 3:
            break
    return hits[:3]


def _corpus_accusations(
    pages: list[SearchResult] | None, company: str
) -> list[tuple[str, str]]:
    """Independent pages that call the company itself a scam, as (domain, quote)."""
    hits: list[tuple[str, str]] = []
    key = _company_key(company)
    for result in pages or []:
        domain = extract_domain(result.url)
        # Checkers have their own verdict, and a company's own site is not an
        # independent accuser.
        if _is_checker(domain) or (key and _company_key(domain) == key):
            continue
        text = _page_text(result)
        if not _mentions_company(text, company):
            continue
        for match in ACCUSATION_RE.finditer(text):
            after = text[match.end() : match.end() + 3].lstrip()
            if after.startswith("?"):
                continue
            # The company has to be the *subject* of the accusing sentence. A
            # generic fraud-advice article mentions a brand without accusing it
            # ("scammers impersonate Infosys"), and a government page listing
            # scam types is not an accusation against every company it names.
            start = max(
                0,
                text.rfind(".", 0, match.start()) + 1,
                text.rfind("\n", 0, match.start()) + 1,
            )
            if not _mentions_company(text[start : match.start()], company):
                continue
            sentence = text[start : match.end() + 80].strip()
            hits.append((domain, sentence[:220]))
            break
    return hits[:4]


def _offer_ties(pages: list[SearchResult] | None, company: str) -> list[str]:
    """Sentences saying the benefit is only for customers of the sender."""
    hits: list[str] = []
    for result in pages or []:
        text = _page_text(result)
        # The page has to be about this company for its terms to be its terms.
        if not _mentions_company(text, company):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            low = sentence.lower()
            if not any(t in low for t in BENEFIT_TERMS):
                continue
            if not any(t in low for t in SERVICE_TERMS):
                continue
            if any(pattern.search(sentence) for pattern in OFFER_TIE_RES):
                hits.append(sentence.strip()[:220])
                break
        if len(hits) >= 2:
            break
    return hits[:2]


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
def detect_patterns(
    user_input: UserInput,
    domain: DomainIntel,
    reviews: ReviewSignals,
    *,
    historical_reports: int = 0,
    sources_checked: int = 0,
    pages: list[SearchResult] | None = None,
) -> list[DetectedPattern]:
    blob = _blob(user_input)
    claims_low = " ".join(user_input.claims).lower()
    found: list[DetectedPattern] = []

    def emit(p: DetectedPattern | None) -> None:
        if p is not None:
            found.append(p)

    # ---- money ----
    money = user_input.money_request
    reason_hit = money.reason and money.reason.lower() in MONEY_REASONS
    # Negation-aware: "No Registration Fee" / "No Deposit" must not count.
    fee_word = has_unnegated(blob, MONEY_REASONS) or has_unnegated(blob, ["fee", "deposit"])
    emit(
        _p(
            "upfront_payment",
            bool(money.detected or (fee_word and money.amount)),
            0.95 if money.detected and (reason_hit or money.amount) else 0.75,
            (
                f"An upfront payment was requested"
                + (f" ({money.amount})" if money.amount else "")
                + (f" described as a {money.reason}." if money.reason else ".")
            ),
            [money.reason] if money.reason else [],
        )
    )
    emit(
        _p(
            "suspicious_reward_offer",
            any(t in blob for t in REWARD_TERMS),
            0.7,
            "A free high-value reward is offered as part of the offer — a common lure.",
            [t for t in REWARD_TERMS if t in blob][:3],
        )
    )
    emit(
        _p(
            "untraceable_payment",
            any(t in blob for t in UNTRACEABLE_TERMS),
            0.85,
            "Payment direction points to an untraceable method (gift card/crypto/wallet).",
            [t for t in UNTRACEABLE_TERMS if t in blob][:3],
        )
    )
    emit(
        _p(
            "fake_check_payment",
            any(pattern.search(blob) for pattern in FAKE_CHECK_PATTERNS),
            0.8,
            "The posting describes a fake-check / forward-the-money pattern.",
        )
    )

    # ---- channels ----
    comms = user_input.communication
    emit(
        _p(
            "whatsapp_recruitment",
            comms.whatsapp,
            0.6 if user_input.company.website else 0.8,
            "Recruitment is being handled over WhatsApp rather than a formal process.",
        )
    )
    emit(
        _p("telegram_recruitment", comms.telegram, 0.6, "Recruitment is being handled over Telegram.")
    )
    no_portal = (comms.whatsapp or comms.telegram) and not user_input.company.website
    emit(
        _p(
            "social_only_recruitment",
            no_portal,
            0.7,
            "There is no official application channel; contact is social/messaging only.",
        )
    )
    # A shortener is a URL fact, not an inference: the destination really cannot
    # be read, so confidence is high (weight is what keeps it contextual).
    emit(
        _p(
            "shortener_link",
            any(_is_shortener_url(u) for u in user_input.contacts.urls),
            0.85,
            "The application link is hidden behind a URL shortener, so its "
            "destination cannot be read.",
        )
    )
    # The posting names an employer but never links to that employer's own
    # domain: candidates are routed to a third-party or obfuscated address
    # instead. Genuine campus drives do use Google Forms and the college's own
    # shortener, so this stays contextual — it can add weight, but it can never
    # by itself justify a HIGH verdict.
    emit(
        _p(
            "unofficial_application_channel",
            bool(user_input.contacts.urls)
            and bool(user_input.company.name)
            and not _links_to_employer_domain(user_input)
            # An official page reproducing the same link verifies the destination,
            # which is exactly what this signal says cannot be done (a government
            # or college notice pointing at the same Google Form is the normal
            # shape of a legitimate campus programme).
            and not reviews.link_verified_by_official_source,
            0.55,
            "The posting names an employer but routes registration through a link "
            "that is not on that employer's own domain.",
            list(user_input.contacts.urls)[:2],
        )
    )

    # ---- claim consistency vs an authoritative source ----
    # "Advertised 4 LPA, the official scheme pays Rs 9,000/month" is a direct
    # contradiction rather than an inference, so it can carry a HIGH verdict when
    # it sits alongside channel evidence, but stays MODERATE on its own.
    emit(
        _p(
            "claim_contradicted_by_official_source",
            reviews.claim_contradicted,
            # 0.6 keeps a contradiction on its own inside MODERATE and, with a
            # hidden registration channel, in HIGH — not CRITICAL. The programme
            # behind such a post is often real; what is unverified is *this*
            # notice, so the wording must not claim "very likely fraudulent".
            0.6,
            "An official source states different terms for this programme than the "
            "posting does (pay, eligibility, or the required application channel).",
        )
    )
    free_mail = any(
        e.split("@")[-1] in {
            "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com",
            "proton.me", "protonmail.com", "icloud.com",
        }
        for e in user_input.contacts.emails
        if "@" in e
    )
    emit(
        _p(
            "free_email_contact",
            free_mail,
            0.5,
            "Recruitment contact uses a personal/free email address rather than the company domain.",
        )
    )

    # ---- personal data ----
    sensitive_hits = _sensitive_hits(blob)
    emit(
        _p(
            "sensitive_data_request",
            bool(sensitive_hits),
            0.9,
            "The request involves highly sensitive financial/identity data.",
            sensitive_hits[:4],
        )
    )
    doc_terms = [t for t in ("aadhaar", "aadhar", "pan card", "passport", "marksheet", "bank passbook") if t in blob]
    emit(
        _p(
            "document_request_pre_offer",
            bool(doc_terms) and not money.detected,
            0.4,
            "Identity documents are requested very early, before any offer stage.",
            doc_terms[:3],
        )
    )

    # ---- opportunity ----
    high, why = is_unusually_high(user_input)
    emit(_p("unusually_high_salary", high, 0.7, why or "Compensation is well above market."))
    # Tolerate wording like "without an interview" / "without any interview".
    no_interview = (
        bool(re.search(r"without\s+(?:an?|any|the)?\s*interview", blob))
        or bool(re.search(r"\bno\s+(?:technical\s+|formal\s+|real\s+)?interview", blob))
        or any(t in blob for t in NO_INTERVIEW_TERMS)
    )
    emit(
        _p(
            "no_interview_selection",
            no_interview,
            0.8,
            "Selection occurred without a meaningful interview or assessment.",
        )
    )
    emit(
        _p(
            "guaranteed_selection",
            any(t in blob for t in GUARANTEE_TERMS),
            0.7,
            "The outcome is described as guaranteed, which legitimate employers do not do.",
        )
    )
    emit(
        _p(
            "urgency_pressure",
            any(t in blob for t in URGENCY_TERMS) and (money.detected or no_interview or comms.whatsapp or comms.telegram),
            0.5,
            "Urgency is being used to rush a decision alongside other risk signals.",
        )
    )
    emit(
        _p(
            "unrealistic_easy_money",
            bool(EASY_MONEY_RE.search(blob)) and any(t in blob for t in NO_EFFORT_TERMS),
            0.7,
            "High pay is promised for trivial, effort-free work — a classic lure.",
        )
    )
    emit(
        _p(
            "reshipping_scheme",
            any(t in blob for t in RESHIPPING_TERMS),
            0.75,
            "The role is to receive and forward packages — a known reshipping/money-mule scheme.",
        )
    )

    # ---- what the captured pages themselves say ----
    # Both of these are read off pages this run actually fetched, so they cite a
    # source the user can open rather than an inference about the posting.
    checker_hits = _checker_flags(pages)
    emit(
        _p(
            "security_checker_flag",
            bool(checker_hits),
            0.85,
            "An independent website-safety checker flags this domain as a scam or "
            "phishing risk.",
            checker_hits,
        )
    )
    tie_hits = _offer_ties(pages, user_input.company.name or "")
    emit(
        _p(
            "offer_tied_to_paid_service",
            bool(tie_hits),
            0.75,
            "The published terms make the benefit conditional on buying through the "
            "sender, so the award is a route to their paid service.",
            tie_hits,
        )
    )
    corpus_acc = _corpus_accusations(pages, user_input.company.name or "")
    accuser_domains = {domain for domain, _ in corpus_acc}
    # One site alleging fraud is a caution. It is corroboration — which an
    # accusation signal is allowed to rest on — when a second independent site
    # says the same, or when the complaint volume independently confirms people
    # are reporting about this company.
    accusation_corroborated = (
        len(accuser_domains) >= 2 or reviews.negative_mentions >= MIN_NEGATIVE_MENTIONS
    )
    emit(
        _p(
            "fraud_accusations_against_company",
            bool(corpus_acc),
            0.8 if accusation_corroborated else 0.7,
            (
                f"{len(accuser_domains)} independent source(s) call this company a scam "
                f"or accuse it of defrauding candidates"
                + (
                    f", corroborated by {reviews.negative_mentions} negative mentions."
                    if accusation_corroborated and reviews.negative_mentions
                    else "."
                )
            ),
            [quote for _, quote in corpus_acc],
            severity="high" if accusation_corroborated else "medium",
        )
    )

    # ---- domain ----
    if domain.domain:
        if domain.age_days is not None and domain.age_days < 180:
            emit(
                _p(
                    "recent_domain",
                    True,
                    0.9 if domain.age_days < 60 else 0.7,
                    f"The website domain was registered about {domain.age_days} days ago.",
                )
            )
        emit(
            _p(
                "domain_name_mismatch",
                domain.company_name_match is False,
                0.65,
                "The domain does not resemble the company name it claims to represent.",
            )
        )
        emit(
            _p(
                "cheap_tld",
                domain.cheap_tld,
                0.35,
                f"Uses a low-cost .{domain.tld} extension (weak supporting signal only).",
            )
        )
        emit(
            _p(
                "domain_unreachable",
                domain.dns_resolves is False or domain.reachable is False,
                0.6,
                "The claimed website does not resolve or cannot be reached.",
            )
        )

    # ---- reputation ----
    emit(
        _p(
            "repeated_payment_complaints",
            reviews.payment_complaints >= 2,
            0.85,
            f"{reviews.payment_complaints} independent sources mention payment demands.",
        )
    )
    emit(
        _p(
            "repeated_channel_complaints",
            reviews.whatsapp_complaints >= 2,
            0.6,
            f"{reviews.whatsapp_complaints} sources mention messaging-app recruitment.",
        )
    )
    neg_signal = _negative_reputation_signal(reviews)
    # The corpus accusations above already cite these same pages, so scoring them
    # again as generic dissatisfaction would count one body of reports twice.
    if neg_signal is not None and not corpus_acc:
        severity, confidence = neg_signal
        emit(
            _p(
                "negative_reputation",
                True,
                confidence,
                _negative_reputation_rationale(reviews),
                severity=severity,
            )
        )
    accusation_signal = _fraud_accusation_signal(reviews)
    if accusation_signal is not None:
        severity, confidence = accusation_signal
        emit(
            _p(
                "fraud_accusations_against_company",
                True,
                confidence,
                _fraud_accusation_rationale(reviews),
                severity=severity,
            )
        )
    emit(
        _p(
            "no_company_footprint",
            sources_checked > 0 and reviews.total_mentions == 0 and bool(user_input.company.name),
            0.55,
            "Searches returned essentially no independent trace of the company.",
        )
    )

    # ---- history ----
    # A single prior report is a weak signal; volume is what matters.
    if historical_reports <= 1:
        hist_severity = "low"
    elif historical_reports <= 3:
        hist_severity = "medium"
    else:
        hist_severity = "high"
    emit(
        _p(
            "historical_reports",
            historical_reports > 0,
            min(0.9, 0.4 + 0.12 * historical_reports),
            f"This company/opportunity appears in {historical_reports} previous report(s) here.",
            severity=hist_severity,
        )
    )

    return found


MIN_NEGATIVE_MENTIONS = 3
MIN_NEGATIVE_SOURCES = 2


def _dissatisfaction_volume(reviews: ReviewSignals) -> int:
    """Negative mentions that are *not* already counted as fraud accusations.

    Accusations are a subset of the negative count (every accuser is a negative
    mention). Scoring both would double-count the same pages, which pushed one
    real case to 95/100 off four reports.
    """
    return max(0, reviews.negative_mentions - reviews.fraud_accusations)


def _negative_reputation_detected(reviews: ReviewSignals) -> bool:
    """Negative reputation needs volume *and* independent corroboration.

    A raw count is easy to inflate from a single site (one forum thread, one
    aggregator). When the source domains are known, require the mentions to come
    from at least two distinct domains; otherwise fall back to the count so
    callers that only provide totals (e.g. the LLM structuring path) still work.
    """
    if _dissatisfaction_volume(reviews) < MIN_NEGATIVE_MENTIONS:
        return False
    domains = {d for d in reviews.negative_source_domains if d}
    if domains:
        return len(domains) >= MIN_NEGATIVE_SOURCES
    return True


def _negative_reputation_signal(reviews: ReviewSignals) -> tuple[str, float] | None:
    """Corroborated *dissatisfaction*, weighted by how many independent sources.

    This measures complaints about the employer — a poor process, unpaid work,
    bad reviews — not proof that it defrauds people. A company can be a famously
    bad employer and still be running a genuine recruitment drive, so this tops
    out below the HIGH band on purpose. Explicit accusations get their own,
    stronger signal in ``_fraud_accusation_signal``.

    Severity is deliberately ``medium``: this is a caution, not a fraud signal, so
    corroborated dissatisfaction reaches MODERATE (25 points) while an unreported
    spread stays a listed, low-weight note (18 points) rather than being dressed
    up as fraud. Anything that *accuses* the company of fraud is scored by
    ``_fraud_accusation_signal`` instead, at a severity that can reach HIGH.
    """
    if not _negative_reputation_detected(reviews):
        return None
    domains = {d for d in reviews.negative_source_domains if d}
    if len(domains) >= MIN_NEGATIVE_SOURCES:
        return "medium", 0.9
    return "medium", 0.65


MIN_FRAUD_ACCUSATIONS = 2


def _fraud_accusation_signal(reviews: ReviewSignals) -> tuple[str, float] | None:
    """Independent sources accusing the company itself of fraud.

    This is what separates "widely reported as a scam" from "poorly reviewed":
    two accusations are cautionary, three or more across independent sources is
    strong enough to justify HIGH on its own. Accusations that all trace to a
    single source stay at ``high``, because volume from one site is not
    corroboration — the same rule the dissatisfaction counter uses.
    """
    count = reviews.fraud_accusations
    if count < MIN_FRAUD_ACCUSATIONS:
        return None
    domains = {d for d in reviews.negative_source_domains if d}
    if domains and len(domains) < MIN_NEGATIVE_SOURCES:
        return "high", 0.6
    if count >= 3:
        return "critical", 0.85
    return "high", 0.75


def _fraud_accusation_rationale(reviews: ReviewSignals) -> str:
    domains = {d for d in reviews.negative_source_domains if d}
    spread = f" across {len(domains)} independent sites" if domains else ""
    return (
        f"{reviews.fraud_accusations} independent source(s){spread} explicitly "
        f"accuse this company of fraud."
    )


def _negative_reputation_rationale(reviews: ReviewSignals) -> str:
    domains = {d for d in reviews.negative_source_domains if d}
    if domains:
        return (
            f"{reviews.negative_mentions} negative/complaint mentions across "
            f"{len(domains)} independent sources found online."
        )
    return f"{reviews.negative_mentions} negative/complaint mentions found online."


def _is_shortener_url(url: str) -> bool:
    from .text_utils import is_shortener

    return is_shortener(extract_domain(url))


def _links_to_employer_domain(user_input: UserInput) -> bool:
    """True when at least one link is on a domain the employer plausibly owns."""
    name = user_input.company.name
    return any(
        not _is_shortener_url(url) and domain_mentions_company(extract_domain(url), name)
        for url in user_input.contacts.urls
    )


def pattern_metadata() -> list[dict[str, str]]:
    return [{"pattern": key, **meta} for key, meta in PATTERN_CATALOG.items()]
