"""Stage 2 — turn JSON 1 into categorized investigation queries.

Rather than one search, we build query *categories* so the evidence we gather
covers existence, reputation, complaints, and the specific suspicious claims.
"""

from __future__ import annotations

from ..config import settings
from ..schemas import SearchQueryGroup, UserInput


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

    add(
        "company_existence",
        [
            f'"{name}" official website' if name else "",
            f'"{name}" about company' if name else "",
            f"site:linkedin.com {name}" if name else "",
        ],
    )
    add(
        "company_reviews",
        [
            f'"{name}" reviews' if name else "",
            f'"{name}" employee reviews' if name else "",
            f'"{name}" glassdoor' if name else "",
        ],
    )
    add(
        "scam_complaints",
        [
            f'"{name}" scam' if name else "",
            f'"{name}" fraud' if name else "",
            f'"{subject}" fake internship' if subject else "",
            f'"{subject}" complaints' if subject else "",
        ],
    )
    add(
        "job_complaints",
        [
            f'"{name}" "{title}" complaint' if name and title else "",
            f'"{name}" hiring scam students' if name else "",
            f'"{name}" fake job offer' if name else "",
        ],
    )
    add(
        "domain_mentions",
        [
            f'"{domain}" scam' if domain else "",
            f'"{domain}" reviews' if domain else "",
            f"{website} legit" if website else "",
        ],
    )
    add(
        "payment_complaints",
        [
            f'"{name}" registration fee' if name else "",
            f'"{name}" "pay" internship fee' if name else "",
            f'"{subject}" refund' if subject else "",
        ],
    )
    add(
        "channel_complaints",
        [
            f'"{name}" WhatsApp recruitment' if name else "",
            f'"{name}" telegram job offer' if name else "",
            f'"{subject}" WhatsApp scam' if subject else "",
        ],
    )

    # Claim-specific queries.
    claim_queries: list[str] = []
    for claim in user_input.claims[:5]:
        claim_queries.append(f'"{subject}" "{claim[:60]}"' if subject else claim[:80])
    if user_input.money_request.detected and name:
        claim_queries.append(f'"{name}" "{user_input.money_request.reason or "fee"}"')
    if user_input.opportunity.salary and name:
        claim_queries.append(f'"{name}" salary "{user_input.opportunity.salary}"')
    add("specific_claims", claim_queries)

    return _limit_groups(groups, settings.max_search_queries)


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
