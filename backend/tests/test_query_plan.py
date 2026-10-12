"""Query-planning tests: what the searches are spent on.

A search is the scarce resource here — a run gets a fixed budget of queries — so
which claims earn one decides what evidence can be found at all. The failure mode
these pin down is a claim query built from a *qualification* sentence ("no active
backlogs", "any branch", "2026 batch"): the phrase search returns pages about the
condition itself — degree explainers, forum questions, job boards — that never
mention the employer, so a search is spent on pages that cannot be evidence and
they come back as the investigation's sources.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.schemas import (
    CommunicationChannels,
    CompanyInfo,
    ContactPoints,
    MoneyRequest,
    OpportunityInfo,
    UserInput,
)
from app.services.query_builder import CLAIM_MECHANISM_TERMS, build_queries


def _user_input(claims: list[str], **overrides) -> UserInput:
    return UserInput(
        company=CompanyInfo(name=overrides.pop("name", "Infosys")),
        opportunity=OpportunityInfo(type="internship", title="Infosys Internship 2026"),
        claims=claims,
        communication=CommunicationChannels(**overrides.pop("communication", {})),
        money_request=MoneyRequest(**overrides.pop("money_request", {})),
        contacts=ContactPoints(
            urls=overrides.pop("urls", []), domains=overrides.pop("domains", [])
        ),
        **overrides,
    )


def _queries(user_input: UserInput) -> dict[str, list[str]]:
    return {group.category: group.queries for group in build_queries(user_input)}


def test_eligibility_conditions_do_not_spend_a_search() -> None:
    """The reported case: a qualification line is not a claim to search for.

    "No active backlogs" is an eligibility condition the model returns as a
    claim. Quoting it searched for backlog explainers, which then appeared in the
    report as sources about the employer.
    """
    plan = _queries(_user_input(["No active backlogs", "Open to any branch"]))
    assert "specific_claims" not in plan


def test_a_claim_is_searched_as_its_mechanic_not_its_sentence() -> None:
    """A prose claim becomes a search anyone would actually word that way.

    The sentence is never quoted: a phrase search for a whole sentence matches
    nothing, while the mechanic is how the reports contradicting it are worded.
    Only one query appears here because the engine's negation test works over a
    48-character window and "without" sits close enough to suppress
    "guaranteed" — the same reading of that sentence the rule engine makes.
    """
    plan = _queries(_user_input(["Selected without interview, guaranteed placement"]))
    assert plan["specific_claims"] == ['"Infosys" "without interview"']
    assert not any("guaranteed placement" in query for query in plan["specific_claims"])


def test_mechanics_from_several_claims_are_each_searched_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct mechanics each earn a search, once, in claim order.

    The global budget is raised here so the assertion is about the claim queries
    themselves rather than about which of them survived the round-robin trim
    (that is pinned separately).
    """
    monkeypatch.setattr(settings, "max_search_queries", 30)
    plan = _queries(_user_input(["Guaranteed placement", "Free laptop on joining"]))
    assert plan["specific_claims"] == ['"Infosys" "guaranteed"', '"Infosys" "free laptop"']


def test_fee_and_inducement_claims_are_searched() -> None:
    """The mechanics the rule engine scores are exactly the ones worth a search."""
    plan = _queries(_user_input(["They asked for a Rs 1,500 registration fee"]))
    assert plan["specific_claims"] == ['"Infosys" "registration fee"']


def test_a_negated_mechanic_is_not_searched() -> None:
    """What the posting says it does *not* do is not a claim to go and search.

    "No interview" is the opposite case and must survive: the negation is part of
    the mechanic, not a denial of it.
    """
    assert "specific_claims" not in _queries(_user_input(["No registration fee required"]))
    assert _queries(_user_input(["Selected with no interview"]))["specific_claims"] == [
        '"Infosys" "no interview"'
    ]


def test_mechanics_do_not_repeat_across_claims() -> None:
    """Three ways of saying the same thing is still one mechanical search."""
    plan = _queries(
        _user_input(["Registration fee of Rs 500", "They want a registration fee upfront"])
    )
    assert plan["specific_claims"] == ['"Infosys" "registration fee"']


def test_claims_are_skipped_but_the_money_request_query_stays() -> None:
    """A structured fee request is still searched when no sentence claims it."""
    plan = _queries(
        _user_input(
            ["No active backlogs"],
            money_request={"detected": True, "amount": "Rs 1,500", "reason": "processing fee"},
        )
    )
    assert plan["specific_claims"] == ['"Infosys" "processing fee"']


def test_a_thin_claim_set_leaves_the_complaint_queries_the_budget() -> None:
    """Skipping a generic claim returns its slot to the searches that matter."""
    plan = _queries(
        _user_input(
            ["No active backlogs"],
            domains=["bit.ly"],
            urls=["https://bit.ly/3xAbCdE"],
        )
    )
    assert "specific_claims" not in plan
    assert plan["scam_complaints"] == ['"Infosys" scam', '"Infosys" fraud']
    assert plan["channel_complaints"] == [
        '"Infosys" WhatsApp recruitment',
        '"Infosys" telegram job offer',
    ]


def test_the_query_budget_is_still_respected() -> None:
    """Filtering claims must not push a plan past the configured budget."""
    plan = _queries(
        _user_input(
            [
                "Registration fee required",
                "Guaranteed placement",
                "Free laptop",
            ],
            money_request={"detected": True, "reason": "registration fee"},
        )
    )
    total = sum(len(queries) for queries in plan.values())
    assert total <= settings.max_search_queries


def test_the_mechanic_vocabulary_matches_the_rule_engine() -> None:
    """One vocabulary, not two: what is searched for is what would be scored."""
    from app.services.patterns import (
        GUARANTEE_TERMS,
        MONEY_REASONS,
        NO_INTERVIEW_TERMS,
        REWARD_TERMS,
    )

    assert set(CLAIM_MECHANISM_TERMS) == (
        MONEY_REASONS | NO_INTERVIEW_TERMS | GUARANTEE_TERMS | REWARD_TERMS
    )
