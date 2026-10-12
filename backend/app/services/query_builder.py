"""Stage 2 — turn JSON 1 into categorized investigation queries.

Rather than one search, we build query *categories* so the evidence we gather
covers existence, reputation, complaints, and the specific suspicious claims.
"""

from __future__ import annotations

from ..config import settings
from ..schemas import SearchQueryGroup, UserInput
from .patterns import GUARANTEE_TERMS, MONEY_REASONS, NO_INTERVIEW_TERMS, REWARD_TERMS
from .text_utils import has_unnegated

# The mechanics a claim has to assert to be worth a search: a fee asked for,
# selection without an interview, a guarantee, an inducement. This is the rule
# engine's own vocabulary (``patterns``), so what gets searched for is what would
# count as evidence — there is no second list to drift out of step.
#
# The model returns eligibility conditions as claims too — "no active backlogs",
# "any branch", "2026 batch" — and quoting one of those spends a search on the
# condition itself: degree explainers, forum questions and job boards that never
# mention the employer, which then come back as the investigation's sources.
CLAIM_MECHANISM_TERMS: tuple[str, ...] = tuple(
    sorted(MONEY_REASONS | NO_INTERVIEW_TERMS | GUARANTEE_TERMS | REWARD_TERMS)
)


def build_queries(user_input: UserInput) -> list[SearchQueryGroup]:
    name = (user_input.company.name or "").strip()
    title = (user_input.opportunity.title or "").strip()
    website = (user_input.company.website or "").strip()
    domain = user_input.contacts.domains[0] if user_input.contacts.domains else ""

    if not name and not domain:
        return []

    subject = name or domain
    groups: list[SearchQueryGroup] = []

    def add(category: str, queries: list[str]) -> None:
        cleaned = [q.strip() for q in queries if q and q.strip()]
        if cleaned:
            groups.append(SearchQueryGroup(category=category, queries=cleaned))

    # A deliberately small, high-signal set: two or three queries per theme.
    # The scam/negative themes come first, so the round-robin budget below
    # always covers them before spending queries on general company info.
    add(
        "scam_complaints",
        [
            f'"{name}" scam' if name else "",
            f'"{name}" fraud' if name else "",
            f'"{subject}" fake internship' if subject else "",
        ],
    )
    add(
        "payment_complaints",
        [
            f'"{name}" registration fee' if name else "",
            f'"{subject}" refund' if subject else "",
        ],
    )
    add(
        "channel_complaints",
        [
            f'"{name}" WhatsApp recruitment' if name else "",
            f'"{name}" telegram job offer' if name else "",
        ],
    )
    add(
        "job_complaints",
        [
            f'"{name}" fake job offer' if name else "",
            f'"{name}" "{title}" complaint' if name and title else "",
            f'"{name}" hiring scam students' if name else "",
        ],
    )
    add(
        "domain_mentions",
        [
            f'"{domain}" scam' if domain else "",
            f"{website} legit" if website else "",
        ],
    )
    add(
        "company_reviews",
        [
            f'"{name}" reviews' if name else "",
            f'"{name}" glassdoor' if name else "",
        ],
    )
    add(
        "company_existence",
        [
            f'"{name}" official website' if name else "",
            f"site:linkedin.com {name}" if name else "",
        ],
    )

    # Claim-specific queries, kept to the strongest few to stay within budget.
    #
    # Each one searches for the *mechanic* the claim asserts rather than the
    # claim sentence. A claim reaches the model as prose ("selected without any
    # interview or test, guaranteed placement") and a phrase search for a whole
    # sentence matches nothing, whereas `"<employer>" "without interview"` is how
    # the reports that contradict it are worded. A claim that asserts no mechanic
    # is not searched at all (see ``CLAIM_MECHANISM_TERMS``).
    claim_terms: list[str] = []
    for claim in user_input.claims:
        for term in _claim_mechanisms(claim):
            if term not in claim_terms:
                claim_terms.append(term)

    claim_queries = [
        f'"{subject}" "{term}"' if subject else f'"{term}"' for term in claim_terms[:2]
    ]
    if user_input.money_request.detected and name:
        claim_queries.append(f'"{name}" "{user_input.money_request.reason or "fee"}"')
    add("specific_claims", claim_queries)

    return _limit_groups(groups, settings.max_search_queries)


def _claim_mechanisms(claim: str) -> list[str]:
    """The scam mechanics a claim actually asserts, in vocabulary order.

    Negation-aware (``has_unnegated``), because what a posting says it does *not*
    do is not something to go and search for: "no registration fee" is not a fee
    claim, while "no interview" is still the selection mechanic it names. This is
    the rule engine's own negation test — the same window, so a claim is searched
    for exactly when the same sentence would be scored for it. A fee the posting
    demands is still searched via ``money_request`` even when a nearby negation
    suppresses its sentence, so nothing that matters is lost.
    """
    return [term for term in CLAIM_MECHANISM_TERMS if has_unnegated(claim, (term,))]


def _limit_groups(groups: list[SearchQueryGroup], limit: int) -> list[SearchQueryGroup]:
    """Trim to a global query budget, round-robin across categories."""
    total = sum(len(g.queries) for g in groups)
    if total <= limit:
        return groups

    trimmed = [SearchQueryGroup(category=g.category, queries=[]) for g in groups]
    index = 0
    while sum(len(g.queries) for g in trimmed) < limit:
        progressed = False
        for source, target in zip(groups, trimmed):
            if index < len(source.queries):
                target.queries.append(source.queries[index])
                progressed = True
                if sum(len(g.queries) for g in trimmed) >= limit:
                    break
        index += 1
        if not progressed:
            break
    return [g for g in trimmed if g.queries]
