"""Stage 5 — structure gathered pages into evidence.

Groq reads the search results and returns a normalized view (review signals,
evidence items, notable findings). A keyword classifier provides the fallback.
"""

from __future__ import annotations

import logging
import re

from ..config import settings
from ..schemas import (
    AnalystFlag,
    AnalystReport,
    Evidence,
    EvidenceCoverage,
    ReviewSignals,
    UserInput,
)
from .exa import SearchResult
from .groq_client import groq_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a fraud-intelligence analyst. You receive web search \
results and page excerpts about a company/job opportunity plus the user's original \
report. Extract the reputation and complaint signals you can actually observe.

Return ONLY JSON:
{
  "reviews": {
    "total_mentions": int,
    "negative_mentions": int,
    "payment_complaints": int,
    "whatsapp_complaints": int,
    "fake_interview_complaints": int,
    "salary_complaints": int,
    "nonpayment_complaints": int
  },
  "evidence": [
    {"type": "review"|"complaint"|"forum"|"website"|"social"|"news",
     "source_url": string,
     "source_domain": string,
     "title": string,
     "summary": string,
     "relevance": number 0..1}
  ],
  "notable_findings": [string],
  "analyst": {
    "fraud_score": int 0..100,
    "verdict": "likely_genuine"|"suspicious"|"fraudulent"|"needs_verification",
    "summary": string,
    "red_flags": [{"point": string, "evidence": string}],
    "green_flags": [{"point": string, "evidence": string}],
    "what_to_do": [string],
    "sources_used": [string]
  }
}

Rules:
- Count only what the provided results actually support. Do not invent counts.
- Only count a result if it is genuinely ABOUT this company (names it) AND is an
  independent third-party account. Never count:
    * the company's own website, careers, security or fraud-awareness pages;
    * generic articles about job/internship scams that do not mention the company;
    * ordinary mentions of words like "pay", "fee", "salary" or "fraud" on
      corporate, payroll or informational pages.
- "payment_complaints"/"whatsapp_complaints" mean reports that the company asked
  candidates for money / recruited over messaging apps, not the words appearing.
- Do not count results that describe the company being IMPERSONATED (phishing
  using its name, scammers "posing as" it, fake profiles using its brand), that
  warn generally about such abuse, or that are its own advisory notices: the
  company is the victim there, not the source of the complaint.
- Negative mentions are only results describing an actual negative candidate
  experience WITH this company as the actor.
- Keep evidence summaries short and factual. Cite the provided URLs only.
- notable_findings: 3-7 concise observations (e.g. "3 results mention upfront fees").
- analyst.fraud_score: 0 = clearly safe, 100 = clearly fraudulent. Judge only
  from the user report and the provided results/sources.
- analyst: do NOT raise the score for remote work, a Gmail address, a shortened
  link, WhatsApp contact or a high salary on their own. Raise it for concrete
  evidence: a fee/credential request, impersonation of a brand or government
  body, or independent reports of fraud. If the deadline has passed but the
  opportunity looks genuine, say so and keep the score low.
- analyst.red_flags/green_flags: each item must cite the evidence it is based on
  (quote or URL). sources_used: the URLs you actually relied on.
- Output JSON only."""


# Only unambiguous complaint language counts as negative on its own. Generic
# words ("report", "warning", "illegal", "pay") appear on legitimate corporate
# pages far too often to be treated as a complaint without other context.
# "complaint" is deliberately absent: ordinary employee gripes ("my complaint is
# the hybrid policy") are not fraud signals. Fraud-specific corroboration is
# required for a page to count against a company.
STRONG_NEGATIVE_TERMS = {
    "scam", "fraud", "fake", "cheated", "cheat", "beware",
    "not paid", "unpaid", "no stipend", "money lost", "looted", "harass",
    "extortion", "advance fee",
}
# Payment complaints must match a *pay-to-get-the-job* structure, not the word
# "pay" on its own (which is ubiquitous — e.g. payroll companies).
PAYMENT_PATTERNS = [
    re.compile(
        r"\b(registration|application|training|processing|certificate|security|joining|"
        r"documentation|verification|equipment|refundable)\s+(fee|deposit|charge)\b"
    ),
    re.compile(
        r"\b(asked|ask|asks|asking|forced|required|demanded|told|charged|charge)\b"
        r"[^.\n]{0,40}\b(pay|paid|payment|fee|deposit|money|amount|rs\.?|₹)\b"
    ),
    re.compile(
        r"\b(pay|paid|paying|payment|fee|deposit)\b[^.\n]{0,30}\b(fee|deposit|money|"
        r"amount|rs\.?|₹|before|upfront|advance|joining|to\s+(?:join|start|confirm))\b"
    ),
    re.compile(
        r"\b(upfront|advance|before\s+joining|to\s+confirm|to\s+get\s+the\s+job)\b"
        r"[^.\n]{0,30}\b(payment|fee|deposit)\b"
    ),
    re.compile(r"\bupi\b|\bgift\s?card\b|\bcrypto\b|\bbitcoin\b"),
]
WHATSAPP_TERMS = {"whatsapp", "telegram", "wa.me", "t.me", "chat app"}
INTERVIEW_TERMS = {"no interview", "without interview", "no technical", "guaranteed selection", "instant offer"}
SALARY_TERMS = {"unrealistic", "too high", "lakh per month", "salary", "stipend"}
NONPAYMENT_TERMS = {"not paid", "unpaid", "no stipend", "salary not", "stipend not"}

# Pages that describe *someone impersonating* the brand (phishing, "posing as",
# "using the company's name") show the brand as the victim, not the perpetrator.
# Counting those against the brand is a classic reputation false positive, so
# they are excluded from negative counts and complaint counters.
IMPERSONATION_PATTERNS = [
    re.compile(r"\bimpersonat\w*"),
    re.compile(r"\bimpost(?:er|or)s?\b"),
    re.compile(r"\b(?:posing|posed|pose) as\b"),
    re.compile(r"\bpretend(?:s|ing|ed)? to be\b"),
    re.compile(r"\bcalled (?:himself|herself|themselves)\b"),
    re.compile(r"\bphish(?:ing|ed|es)?\b"),
    re.compile(r"\bspoof\w*"),
    re.compile(r"\b(?:look[\s-]?alike|clone[d]?)\b"),
    re.compile(r"\bin the name of\b"),
    re.compile(r"\busing (?:the )?(?:name|identity|brand|logo|letterhead|goodwill) of\b"),
    re.compile(r"\bsomeone (?:is )?(?:using|claiming|pretending)\b"),
    re.compile(r"\bnot (?:affiliated|associated|connected) with\b"),
    # Reports that a *posting/offer* is fake (i.e. the brand's name is being
    # abused) rather than that the brand itself defrauded someone.
    re.compile(r"\bfake or true\b"),
    re.compile(r"\bfake (?:job|jobs|opening|posting|offer|profile|account|recruiter)s?\b"),
]

# Generic host labels that say nothing about which company a page is about.
GENERIC_HOST_WORDS = {
    "www", "jobs", "job", "careers", "career", "apply", "hiring", "recruit",
    "recruitment", "mail", "email", "info", "support", "get", "the", "app",
}
COMPANY_STOPWORDS = {
    "inc", "llc", "ltd", "limited", "pvt", "private", "technologies", "technology",
    "solutions", "services", "company", "group", "corp", "corporation", "india",
    "the", "and", "global", "international",
}


def _topic_tokens(user_input: UserInput) -> set[str]:
    """Tokens that indicate a result is actually about this company.

    Used to discard generic scam-advice pages that a search for "<company> scam"
    inevitably returns but that never mention the company at all.

    When the company name is known we filter on the *name* only. Including the
    URL domain would let an unrelated third-party platform (an assessment site,
    a shortener) become the filter key and silently discard every page about the
    actual company — the bug that hid real scam reports about "Levroxen LLC"
    because the post's only link was an assessment platform.
    """
    name_tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", (user_input.company.name or "").lower()):
        if len(word) >= 3 and word not in COMPANY_STOPWORDS:
            name_tokens.add(word)
    if name_tokens:
        return name_tokens

    # No company name available: fall back to the domain label (best effort).
    tokens: set[str] = set()
    for domain in user_input.contacts.domains:
        label = domain.split(".")[0].lower()
        for word in re.findall(r"[a-z0-9]+", label):
            if len(word) >= 4 and word not in GENERIC_HOST_WORDS:
                tokens.add(word)
    return tokens


def _matches_topic(blob: str, tokens: set[str]) -> bool:
    if not tokens:
        return True  # nothing to filter on — keep the result
    return any(token in blob for token in tokens)


def _looks_negative(blob: str) -> bool:
    return any(term in blob for term in STRONG_NEGATIVE_TERMS)


def _company_domains(user_input: UserInput) -> set[str]:
    domains: set[str] = set()
    for domain in user_input.contacts.domains:
        if not domain:
            continue
        domain = domain.lower()
        domains.add(domain)
        parts = domain.split(".")
        if len(parts) > 2:  # also treat the registrable domain (jobs.adp.com -> adp.com)
            domains.add(".".join(parts[-2:]))
    return domains


def _is_own_domain(source_domain: str | None, company_domains: set[str]) -> bool:
    """True when a result comes from the company's own site.

    A company's own fraud-awareness or security page is not independent
    evidence against it, so such pages are never counted as complaints.
    """
    if not source_domain:
        return False
    host = source_domain.lower()
    return any(host == d or host.endswith("." + d) for d in company_domains)


def _looks_like_payment_complaint(blob: str) -> bool:
    return any(pattern.search(blob) for pattern in PAYMENT_PATTERNS)


def _looks_like_impersonation(blob: str) -> bool:
    """True when a page describes the brand being impersonated (victim, not actor)."""
    return any(pattern.search(blob) for pattern in IMPERSONATION_PATTERNS)


def heuristic_structure(
    results: list[SearchResult], user_input: UserInput
) -> tuple[ReviewSignals, list[Evidence], list[str]]:
    reviews = ReviewSignals()
    evidence: list[Evidence] = []
    negatives = 0
    negative_domains: list[str] = []
    tokens = _topic_tokens(user_input)
    company_domains = _company_domains(user_input)

    for result in results:
        blob = f"{result.title} {result.snippet}".lower()
        reviews.total_mentions += 1

        # A result only counts against the company if it is actually about it.
        # Searches like "<company> scam" otherwise return generic scam-advice
        # pages that never mention the company at all. Mock results are exempt.
        on_topic = result.is_mock or _matches_topic(blob, tokens)
        own_domain = _is_own_domain(result.source_domain, company_domains)

        # Prefer an explicit sentiment (mock data) over keyword guessing, which
        # otherwise misreads polite negations like "no complaints found".
        if result.sentiment == "negative":
            is_negative = True
        elif result.sentiment in {"neutral", "positive"}:
            is_negative = False
        else:
            is_negative = on_topic and not own_domain and _looks_negative(blob)

        # A report that the brand is being *impersonated* (phishing, "posing as",
        # "using its name") casts the brand as the victim, not the culprit, so it
        # must not count as the brand's negative reputation. Mock data carries an
        # explicit sentiment and is exempt so deterministic fixtures stay stable.
        impersonation = is_negative and not result.is_mock and _looks_like_impersonation(blob)
        if impersonation:
            is_negative = False

        if is_negative:
            negatives += 1
            if result.source_domain and result.source_domain not in negative_domains:
                negative_domains.append(result.source_domain)

        # Complaint counters require a genuinely negative, on-topic result.
        if is_negative and on_topic:
            if _looks_like_payment_complaint(blob):
                reviews.payment_complaints += 1
            if any(t in blob for t in WHATSAPP_TERMS):
                reviews.whatsapp_complaints += 1
            if any(t in blob for t in INTERVIEW_TERMS):
                reviews.fake_interview_complaints += 1
            if any(t in blob for t in SALARY_TERMS):
                reviews.salary_complaints += 1
            if any(t in blob for t in NONPAYMENT_TERMS):
                reviews.nonpayment_complaints += 1

        if result.url and result.url not in reviews.source_urls:
            reviews.source_urls.append(result.url)

        include = bool(is_negative) or (
            result.sentiment is None
            and on_topic
            and not impersonation
            and result.category in {"scam_complaints", "payment_complaints", "channel_complaints"}
        )
        if include and len(evidence) < 24:
            evidence.append(
                Evidence(
                    type=_evidence_type(result.category),
                    source_url=result.url,
                    source_domain=result.source_domain,
                    title=result.title,
                    summary=result.snippet[:400],
                    relevance=0.85 if is_negative else 0.5,
                )
            )

    reviews.negative_mentions = negatives
    reviews.negative_source_domains = negative_domains
    reviews.source_urls = reviews.source_urls[:40]

    findings: list[str] = []
    if reviews.payment_complaints:
        findings.append(f"{reviews.payment_complaints} sources mention payment requests.")
    if reviews.whatsapp_complaints:
        findings.append(f"{reviews.whatsapp_complaints} sources mention messaging-app recruitment.")
    if reviews.fake_interview_complaints:
        findings.append(f"{reviews.fake_interview_complaints} sources mention selection without a real interview.")
    if negatives:
        findings.append(f"{negatives} negative/complaint mentions found across {reviews.total_mentions} results examined.")
    else:
        findings.append(f"{reviews.total_mentions} results examined; no strong negative reputation signal.")
    if user_input.company.name and not findings:
        findings.append(f"Limited public information was available for {user_input.company.name}.")

    return reviews, evidence, findings


def _evidence_type(category: str) -> str:
    return {
        "scam_complaints": "complaint",
        "payment_complaints": "complaint",
        "channel_complaints": "forum",
        "company_reviews": "review",
        "job_complaints": "complaint",
        "domain_mentions": "website",
    }.get(category, "web")


def _analyst_flag(raw) -> AnalystFlag | None:
    if isinstance(raw, str) and raw.strip():
        return AnalystFlag(point=raw.strip()[:300])
    if isinstance(raw, dict):
        point = str(raw.get("point") or raw.get("flag") or "").strip()
        if point:
            return AnalystFlag(point=point[:300], evidence=str(raw.get("evidence") or "")[:300])
    return None


def _parse_analyst(data) -> AnalystReport | None:
    """Parse the analyst block, tolerating loose model output."""
    if not isinstance(data, dict):
        return None
    try:
        score = int(float(data.get("fraud_score") or 0))
    except (TypeError, ValueError):
        score = 0
    red = [_analyst_flag(f) for f in (data.get("red_flags") or [])]
    green = [_analyst_flag(f) for f in (data.get("green_flags") or [])]
    return AnalystReport(
        fraud_score=max(0, min(100, score)),
        verdict=str(data.get("verdict") or "needs_verification")[:40],
        summary=str(data.get("summary") or "")[:800],
        red_flags=[f for f in red if f][:12],
        green_flags=[f for f in green if f][:12],
        what_to_do=[str(x)[:200] for x in (data.get("what_to_do") or [])][:8],
        sources_used=[str(x)[:400] for x in (data.get("sources_used") or [])][:20],
    )


# Appended to the system prompt when the caller opts out of the analyst block.
# Skipping it saves output tokens (and fits tighter TPM budgets) while keeping
# the same reviews/evidence extraction.
NO_ANALYST_INSTRUCTION = (
    "\n- Do NOT include the \"analyst\" object in the output. Return only "
    "\"reviews\", \"evidence\" and \"notable_findings\"."
)


async def structure_evidence(
    results: list[SearchResult],
    user_input: UserInput,
    *,
    include_analyst: bool = True,
) -> tuple[ReviewSignals, list[Evidence], list[str], AnalystReport | None, EvidenceCoverage]:
    """Return (reviews, evidence, notable_findings, analyst_report, coverage).

    The same Groq call that structures the evidence also produces the analyst's
    reasoned, rated read of the searched pages — so a search-then-reason flow
    costs no extra request. Set ``include_analyst=False`` to skip the analyst
    block entirely (frugal mode); it can be fetched later via ``/analyst``.

    Whatever the model returns, the deterministic rules are always run over the
    captured pages the model never saw, and their findings are folded in. That
    way evidence cannot vanish just because it did not fit the prompt.
    """
    captured = len(results)
    topical = has_topical_evidence(results, user_input)
    # Cost control: if no captured page is even about this company, there is
    # nothing for the model to structure — pay for the answer only when there
    # is evidence. The caller then surfaces an inconclusive result.
    worth_calling = topical or not settings.skip_groq_when_evidence_irrelevant

    if groq_client.enabled and results and worth_calling:
        payload, included, signal_sentences = render_prompt(results, user_input)
        user_prompt = (
            f"USER REPORT:\n{user_input.raw_text[:2000]}\n\n"
            f"COMPANY: {user_input.company.name}\n"
            f"WEBSITE: {user_input.company.website}\n\n"
            f"SEARCH RESULTS:\n{payload}"
        )
        system_prompt = SYSTEM_PROMPT if include_analyst else SYSTEM_PROMPT + NO_ANALYST_INSTRUCTION
        # Room for the reviews + evidence + notable_findings + the analyst block;
        # too small a budget truncates the JSON and drops the whole structuring.
        parsed = await groq_client.chat_json(system_prompt, user_prompt, max_tokens=4500)
        if isinstance(parsed, dict):
            try:
                reviews = ReviewSignals(**(parsed.get("reviews") or {}))
                evidence = [
                    Evidence(**e)
                    for e in (parsed.get("evidence") or [])
                    if isinstance(e, dict) and e.get("source_url")
                ]
                findings = [str(f)[:300] for f in (parsed.get("notable_findings") or [])]
                analyst = _parse_analyst(parsed.get("analyst")) if include_analyst else None
                # Sanity: never report more negatives than mentions.
                reviews.negative_mentions = min(reviews.negative_mentions, reviews.total_mentions)
                # Keep the structured result when the analyst was produced even if
                # the counters came back empty (e.g. a single positive page).
                if reviews.total_mentions or evidence or analyst is not None:
                    coverage = _coverage(captured, included, signal_sentences, topical)
                    # Pages outside the prompt are still analysed by the rules.
                    unseen = _unseen(results, included)
                    if unseen:
                        u_reviews, u_evidence, u_findings = heuristic_structure(
                            unseen, user_input
                        )
                        reviews = _merge_reviews(reviews, u_reviews)
                        evidence = _merge_evidence(evidence, u_evidence)
                        findings = list(dict.fromkeys([*findings, *u_findings]))
                    return reviews, evidence[:30], findings[:10], analyst, coverage
            except Exception as exc:  # noqa: BLE001
                logger.warning("Groq evidence structuring failed: %s", exc)

    reviews, evidence, findings = heuristic_structure(results, user_input)
    return reviews, evidence, findings, None, _coverage(captured, [], 0, topical)


# Most pages are represented by their signal sentences (see ``_signal_spans``).
# A page with no recognisable signal falls back to this many leading characters
# so the model still sees what the page is, without sending a whole scrape.
# The rendered total is capped by ``settings.groq_evidence_max_chars`` so one
# investigation stays within Groq's free-tier tokens-per-minute budget.
PER_RESULT_CHARS = 2000

# How much each search category tends to carry fraud-relevant signal. Used only
# to order pages when the budget cannot fit them all.
CATEGORY_WEIGHT = {
    "scam_complaints": 1.2,
    "payment_complaints": 1.2,
    "job_complaints": 1.0,
    "channel_complaints": 0.7,
    "browser_capture": 0.5,
    "company_reviews": 0.3,
    "domain_mentions": 0.2,
    "company_existence": 0.2,
}


def _page_relevance(result: SearchResult, tokens: set[str], company_domains: set[str]) -> float:
    """Score one captured page so the budget goes to the pages that matter.

    The budget can only hold a few pages, so *which* pages are kept decides the
    verdict. Scoring reuses the same signal vocabulary as the deterministic
    engine, so selection and counting agree on what evidence looks like.
    """
    blob = f"{result.title} {result.snippet}".lower()
    score = CATEGORY_WEIGHT.get(result.category, 0.4)

    # A page that never names the company cannot corroborate anything about it:
    # a search for "<company> scam" returns generic advice pages that swamp a
    # small budget unless they are pushed to the back.
    on_topic = result.is_mock or _matches_topic(blob, tokens)
    score += 3.0 if on_topic else -1.0

    # A page describing the brand being impersonated shows it as the victim.
    if not result.is_mock and _looks_like_impersonation(blob):
        score -= 3.0
    # The company's own site is not independent evidence against it.
    if _is_own_domain(result.source_domain, company_domains):
        score -= 1.5

    # An explicit sentiment (mock/search providers) beats keyword guessing,
    # which otherwise misreads negations like "no complaints found".
    if result.sentiment == "negative":
        score += 2.0
    elif result.sentiment in {"neutral", "positive"}:
        score -= 0.5
    elif _looks_negative(blob):
        score += 2.0

    if _looks_like_payment_complaint(blob):
        score += 1.5
    if any(t in blob for t in WHATSAPP_TERMS):
        score += 0.5
    if any(t in blob for t in INTERVIEW_TERMS):
        score += 0.5
    return score


def _dedupe_key(result: SearchResult) -> str:
    """Collapse republished mirrors while keeping genuinely distinct pages.

    Mirrors share both a title and an opening, whereas distinct pages that only
    share boilerplate (nav/cookie/footer noise) differ in title and lead, so
    hashing title + the start of the content separates the two cases.
    """
    title = re.sub(r"\s+", " ", (result.title or "").strip().lower())
    opening = re.sub(r"\s+", " ", (result.snippet or "").strip().lower())[:300]
    return f"{title}\x00{opening}"


def select_results_for_prompt(
    results: list[SearchResult], user_input: UserInput
) -> list[SearchResult]:
    """Rank captured pages by relevance and drop near-duplicate mirrors.

    Deterministic: ties keep their original capture order.
    """
    tokens = _topic_tokens(user_input)
    company_domains = _company_domains(user_input)
    ranked = sorted(
        enumerate(results),
        key=lambda item: (-_page_relevance(item[1], tokens, company_domains), item[0]),
    )
    selected: list[SearchResult] = []
    seen: set[str] = set()
    for _index, result in ranked:
        key = _dedupe_key(result)
        if key in seen:
            continue
        seen.add(key)
        selected.append(result)
    return selected


# Each selected page contributes its signal-bearing sentences rather than its
# first N characters. This is what lets *every* captured page be represented
# inside one budget instead of only the first few, without sending the
# navigation/cookie/footer noise that makes up most of a scraped page.
SIGNAL_SENTENCE_CHARS = 400
MAX_SIGNAL_SENTENCES = 6

# Sentences worth keeping: the same vocabulary the deterministic engine counts
# on, so selection and scoring agree on what evidence looks like.
_SIGNAL_TERMS = STRONG_NEGATIVE_TERMS | WHATSAPP_TERMS | INTERVIEW_TERMS | {
    "impersonat",
    "phishing",
    "posing as",
    "registration fee",
    "security deposit",
}


def _signal_spans(result: SearchResult, tokens: set[str]) -> list[str]:
    """Signal-bearing sentences from a captured page, in capture order.

    When the company name is known a span must also be about the company, which
    keeps trailing "follow us on WhatsApp" navigation boilerplate out of the
    prompt while preserving the sentences that actually name the company.
    """
    text = (result.snippet or "").strip()
    if not text:
        return []
    spans: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = raw.strip()
        if len(sentence) < 20:
            continue
        low = sentence.lower()
        on_topic = result.is_mock or not tokens or _matches_topic(low, tokens)
        strong = _looks_negative(low) or _looks_like_payment_complaint(low)
        if strong or (on_topic and any(term in low for term in _SIGNAL_TERMS)):
            spans.append(sentence[:SIGNAL_SENTENCE_CHARS])
        if len(spans) >= MAX_SIGNAL_SENTENCES:
            break
    return spans


def _page_content(result: SearchResult, tokens: set[str]) -> str:
    """The evidence text to send for one page: its signal, or a short lead."""
    spans = _signal_spans(result, tokens)
    if spans:
        return " … ".join(spans)
    return (result.snippet or "").strip()[:PER_RESULT_CHARS]


def render_prompt(
    results: list[SearchResult], user_input: UserInput
) -> tuple[str, list[SearchResult], int]:
    """Render the prompt, the pages it contains, and their signal-sentence count.

    Pages are selected by relevance rather than capture order, mirrors are
    collapsed, and each page contributes its signal rather than its opening
    characters. Together that means the pages carrying the decisive evidence
    reach the model even when far more pages were captured than the budget
    could hold verbatim. The rendered prompt never exceeds the configured
    budget.
    """
    budget = max(0, settings.groq_evidence_max_chars)
    tokens = _topic_tokens(user_input)
    blocks: list[str] = []
    included: list[SearchResult] = []
    signal_sentences = 0
    used = 0
    for result in select_results_for_prompt(results, user_input):
        title = (result.title or "")[:200]
        header = (
            f"[{len(blocks) + 1}] category={result.category} url={result.url}\n"
            f"TITLE: {title}\nCONTENT: "
        )
        separator = 2 if blocks else 0  # the "\n\n" between blocks
        room = budget - used - separator - len(header)
        if room <= 0:
            break
        content = _page_content(result, tokens)[:room]
        block = header + content
        used += separator + len(block)
        blocks.append(block)
        included.append(result)
        signal_sentences += len(_signal_spans(result, tokens))
    return "\n\n".join(blocks), included, signal_sentences


def _render_results(results: list[SearchResult], user_input: UserInput) -> str:
    """The prompt text alone (see :func:`render_prompt`)."""
    return render_prompt(results, user_input)[0]


def has_topical_evidence(results: list[SearchResult], user_input: UserInput) -> bool:
    """True when at least one captured page is actually about this company.

    A search for "<company> scam" returns generic scam-awareness pages that
    never name the company; those carry no company-specific evidence, so a run
    built only on them is inconclusive rather than clean.
    """
    tokens = _topic_tokens(user_input)
    if not tokens:
        return bool(results)  # cannot judge topicality; assume usable
    return any(
        r.is_mock or _matches_topic(f"{r.title} {r.snippet}".lower(), tokens)
        for r in results
    )


def _unseen(
    results: list[SearchResult], included: list[SearchResult]
) -> list[SearchResult]:
    """Captured pages that the model's prompt did not contain."""
    seen = {id(r) for r in included}
    return [r for r in results if id(r) not in seen]


def _merge_reviews(primary: ReviewSignals, extra: ReviewSignals) -> ReviewSignals:
    """Fold rule-engine findings from pages the model never saw into its own.

    Additive by construction: ``extra`` covers only the *unseen* pages, so it
    cannot double-count anything the model already judged. The model's counts
    are deliberately **not** clamped down to the rule-engine counts — the model
    reads context the rules cannot, so overriding it would manufacture errors
    rather than correct them. The only sanitation is the invariant that there
    cannot be more negative mentions than mentions.
    """
    merged = ReviewSignals(
        total_mentions=primary.total_mentions + extra.total_mentions,
        negative_mentions=primary.negative_mentions + extra.negative_mentions,
        payment_complaints=primary.payment_complaints + extra.payment_complaints,
        whatsapp_complaints=primary.whatsapp_complaints + extra.whatsapp_complaints,
        fake_interview_complaints=(
            primary.fake_interview_complaints + extra.fake_interview_complaints
        ),
        salary_complaints=primary.salary_complaints + extra.salary_complaints,
        nonpayment_complaints=primary.nonpayment_complaints + extra.nonpayment_complaints,
        negative_source_domains=list(
            dict.fromkeys(primary.negative_source_domains + extra.negative_source_domains)
        ),
        source_urls=list(dict.fromkeys(primary.source_urls + extra.source_urls)),
    )
    merged.negative_mentions = min(merged.negative_mentions, merged.total_mentions)
    return merged


def _merge_evidence(
    primary: list[Evidence], extra: list[Evidence]
) -> list[Evidence]:
    """Append rule-derived evidence for unseen pages, without duplicating URLs."""
    seen = {e.source_url for e in primary if e.source_url}
    merged = list(primary)
    for item in extra:
        if item.source_url and item.source_url in seen:
            continue
        if item.source_url:
            seen.add(item.source_url)
        merged.append(item)
    return merged


def _coverage(
    captured: int,
    included: list[SearchResult],
    signal_sentences: int,
    topical: bool,
) -> EvidenceCoverage:
    """Describe how much of the captured evidence the verdict actually rests on."""
    if not captured:
        note = "No pages were captured for this investigation."
    elif not topical:
        note = "No captured page was about this company, so the result is inconclusive."
    else:
        note = ""
    return EvidenceCoverage(
        pages_captured=captured,
        pages_in_prompt=len(included),
        pages_rules_only=captured - len(included),
        signal_sentences=signal_sentences,
        topical=topical,
        sufficient=bool(captured) and topical,
        note=note,
    )
