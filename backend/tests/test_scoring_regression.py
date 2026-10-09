"""Regression guards for link scoring — the "hidden registration link" bug.

The engine used to credit **any** posting containing a URL with +20 trust
("The application link points at the employer's own domain"): the check compared
the application domain against domains harvested from the posting itself, so the
link always matched itself. A posting whose only registration route was a
`tinyurl` therefore came out as LOW RISK with a 70/100 trust score, while the
evidence underneath it said the link could not be verified.

These tests pin the corrected behaviour: identity is only credited for a domain
that is *attributable* to the named employer, and a link that is not the
employer's own is scored as a signal rather than rewarded as a positive.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.schemas import DomainIntel, ReviewSignals
from app.services.extraction import heuristic_extract
from app.services.patterns import detect_patterns
from app.services.risk import HARD_PATTERNS, assess
from app.services.text_utils import domain_mentions_company, registrable_label

FIXED_TODAY = date(2026, 10, 5)  # matches the seed pack's date_checked

POSTS = json.loads(
    (Path(__file__).parent / "fixtures" / "seed_posts.json").read_text(encoding="utf-8")
)["posts"]
BY_ID = {p["id"]: p for p in POSTS}

# The user's own report: a campus-drive notice for an Infosys internship whose
# only registration route is a shortened link, and whose employer domain never
# appears anywhere in the text.
HIDDEN_LINK_POST = BY_ID["JP-003"]["text"]


def _run(text: str):
    user_input = heuristic_extract(text)
    patterns = detect_patterns(
        user_input, DomainIntel(), ReviewSignals(total_mentions=0), sources_checked=0
    )
    risk = assess(
        patterns,
        user_input=user_input,
        evidence=[],
        sources_checked=0,
        used_mock=True,
        today=FIXED_TODAY,
    )
    return user_input, patterns, risk


def test_hidden_registration_link_is_not_credited_as_the_employers_domain() -> None:
    """The reported bug: a tinyurl "matched the employer's own domain"."""
    _ui, _patterns, risk = _run(HIDDEN_LINK_POST)
    assert "application_domain_matches_employer" not in {
        s.id for s in risk.trust_signals
    }
    # Absence-based signals may still be earned; identity may not.
    assert "official_source_found" not in {s.id for s in risk.trust_signals}
    assert risk.trust_score < 60, "unverifiable link must not produce strong trust"


def test_hidden_registration_link_is_scored_as_a_signal() -> None:
    _ui, patterns, risk = _run(HIDDEN_LINK_POST)
    ids = {p.pattern for p in patterns}
    assert {"shortener_link", "unofficial_application_channel"} <= ids
    assert risk.level == "MODERATE", risk.level
    assert risk.score >= 20


def test_a_link_alone_never_reaches_high_risk() -> None:
    """Context, not proof: genuine drives use Google Forms and shorteners."""
    assert "unofficial_application_channel" not in HARD_PATTERNS
    assert "shortener_link" not in HARD_PATTERNS
    _ui, _patterns, risk = _run(HIDDEN_LINK_POST)
    assert risk.level != "HIGH"


def test_unnamed_employer_with_a_foreign_link_is_flagged() -> None:
    text = (
        "Company: Infosys\nRole: AI Intern\nDuration: 6 months\n"
        "Register here: https://quickhyre-jobs.com/apply\n"
        "Selection Process: 1. Written Test 2. HR Interview\n"
        "Eligibility: 2026 batch, 68%"
    )
    _ui, patterns, risk = _run(text)
    assert "unofficial_application_channel" in {p.pattern for p in patterns}
    assert risk.level != "HIGH"


def test_employer_owned_link_still_earns_the_trust_signal() -> None:
    """The fix must not stop a real "apply on the employer's site" posting."""
    _ui, patterns, risk = _run(BY_ID["JP-010"]["text"])  # ADP, jobs.adp.com
    assert "application_domain_matches_employer" in {s.id for s in risk.trust_signals}
    assert "unofficial_application_channel" not in {p.pattern for p in patterns}


@pytest.mark.parametrize("post_id", ["JP-003", "JP-006", "JP-007", "JP-008", "JP-009", "JP-011", "JP-012"])
def test_unattributable_links_never_earn_employer_trust(post_id: str) -> None:
    """Every one of these links is on a third party's domain, not the employer's."""
    _ui, _patterns, risk = _run(BY_ID[post_id]["text"])
    assert "application_domain_matches_employer" not in {
        s.id for s in risk.trust_signals
    }, post_id


@pytest.mark.parametrize(
    ("domain", "name", "expected"),
    [
        ("infosys.com", "Infosys", True),
        ("infosys.com", "Infosys Limited", True),
        ("careers.adobe.com", "Adobe", True),
        ("jobs.adp.com", "ADP", True),  # short brand name, own subdomain
        ("eteaminc.com", "eTeam", True),
        ("mahindra.com", "Tech Mahindra", True),
        ("mjcollege.ac.in", "MJ College", True),
        ("tinyurl.com", "Infosys", False),
        ("t.me", "Google", False),
        ("docs.google.com", "IIIT Guwahati", False),
        ("forms.gle", "CSRBOX", False),
        ("filesusr.com", "Feuji", False),
        ("eej.at", "Myntra", False),
        ("lnkd.in", "HCLTech", False),
        ("exametryx.com", "Levroxen LLC", False),
        ("infosys-careers.xyz", "Infosys", False),  # lookalike, not owned
        ("quickhyre-jobs.com", "QuickHyre AI", True),
    ],
)
def test_domain_attribution(domain: str, name: str, expected: bool) -> None:
    assert domain_mentions_company(domain, name) is expected


def _reputation_risk(**review_kwargs):
    user_input = heuristic_extract(
        "Company: Repco\nRole: AI Intern\nEligibility: 2026 batch, 68%\n"
        "Selection Process: 1. Written Test 2. HR Interview"
    )
    reviews = ReviewSignals(total_mentions=20, **review_kwargs)
    patterns = detect_patterns(user_input, DomainIntel(), reviews, sources_checked=20)
    risk = assess(
        patterns,
        user_input=user_input,
        evidence=[],
        sources_checked=20,
        used_mock=False,
    )
    return {p.pattern for p in patterns}, risk


def test_fraud_accusations_are_scored_apart_from_employee_complaints() -> None:
    """A badly-reviewed employer is not a company accused of fraud.

    Two shapes that look alike in a count of "negative mentions" but must not
    rate alike: a genuine programme run by a company with poor Glassdoor /
    AmbitionBox reviews (dissatisfaction), and a staffing firm that several
    independent sites accuse of scamming candidates (fraud accusations).
    """
    ids, risk = _reputation_risk(
        negative_mentions=6,
        negative_source_domains=["glassdoor.com", "ambitionbox.com"],
    )
    assert "negative_reputation" in ids  # reported honestly...
    assert "fraud_accusations_against_company" not in ids  # ...but not as fraud
    assert risk.level != "HIGH", risk.level

    ids, risk = _reputation_risk(
        negative_mentions=6,
        fraud_accusations=4,
        negative_source_domains=["bbb.org", "trustindex.io", "linkedin.com"],
    )
    assert "fraud_accusations_against_company" in ids
    assert risk.level in {"HIGH", "CRITICAL"}, risk.level
    assert any("accuse this company" in line for line in risk.verified)


def test_accusations_from_a_single_source_are_not_corroboration() -> None:
    """Volume with one origin is not independent evidence."""
    ids, risk = _reputation_risk(
        negative_mentions=6,
        fraud_accusations=4,
        negative_source_domains=["one-forum.example"],
    )
    assert "fraud_accusations_against_company" in ids
    assert risk.level != "HIGH", risk.level


@pytest.mark.asyncio
async def test_history_counts_reports_not_our_own_lookups() -> None:
    """A repeated check must not look like a prior report.

    ``count_historical_reports`` used to add earlier investigations and captured
    pages to the total, so a genuine employer that had been checked a few times
    scored MODERATE off its own history (a stored ADP run reached 43 that way).
    """
    from uuid import uuid4

    from app.db import SessionLocal, engine
    from app.models import Base, Company, InvestigationRecord, UserReport
    from app.services.repository import count_historical_reports
    from app.services.text_utils import normalize_name

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    name = f"Repeatco {uuid4().hex[:8]}"
    user_input = heuristic_extract(f"Company: {name}\nApply here: https://repeatco.example/careers")

    async with SessionLocal() as session:
        company = Company(name=name, name_normalized=normalize_name(name))
        session.add(company)
        await session.flush()
        for _ in range(3):  # our own earlier lookups
            session.add(
                InvestigationRecord(
                    company_id=company.id,
                    status="completed",
                    raw_input="checked before",
                    user_input={},
                    investigation={},
                    risk={},
                )
            )
        await session.commit()
        assert await count_historical_reports(session, user_input) == 0

        session.add(
            UserReport(
                company_id=company.id,
                report_type="user_report",
                description="asked me for a registration fee",
                source="self_reported",
            )
        )
        await session.commit()
        assert await count_historical_reports(session, user_input) == 1


def test_reputation_findings_are_always_reported() -> None:
    """Whatever it scores, a reputation finding must be visible in the result.

    Dissatisfaction is a caution, not fraud, so it is weighted accordingly — but
    it must never be dropped from the signal list, and once it is corroborated by
    independent sources it belongs in the MODERATE band.
    """
    user_input = heuristic_extract(
        "Company: Repco\nRole: AI Intern\nEligibility: 2026 batch, 68%\nSelection Process: 1. Test 2. HR"
    )

    thin = ReviewSignals(total_mentions=9, negative_mentions=3)  # spread unreported
    patterns = detect_patterns(user_input, DomainIntel(), thin, sources_checked=9)
    risk = assess(
        patterns, user_input=user_input, evidence=[], sources_checked=9, used_mock=False
    )
    assert any(s.id == "negative_reputation" for s in risk.signals)  # never hidden

    corroborated = ReviewSignals(
        total_mentions=9,
        negative_mentions=4,
        negative_source_domains=["glassdoor.com", "ambitionbox.com"],
    )
    patterns = detect_patterns(user_input, DomainIntel(), corroborated, sources_checked=9)
    risk = assess(
        patterns, user_input=user_input, evidence=[], sources_checked=9, used_mock=False
    )
    assert risk.level in {"MODERATE", "HIGH", "CRITICAL"}, risk.level


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("jobs.adp.com", "adp"),
        ("mjcollege.ac.in", "mjcollege"),
        ("www-feuji-com.filesusr.com", "filesusr"),
        ("tinyurl.com", "tinyurl"),
        ("t.me", "t"),
        (None, ""),
    ],
)
def test_registrable_label(domain, expected: str) -> None:
    assert registrable_label(domain) == expected
