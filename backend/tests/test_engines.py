"""Unit tests for the deterministic parts of the pipeline (no network)."""

from __future__ import annotations

import pytest

from app.schemas import (
    CommunicationChannels,
    CompanyInfo,
    DetectedPattern,
    DomainIntel,
    Evidence,
    MoneyRequest,
    OpportunityInfo,
    ReviewSignals,
    UserInput,
)
from app.config import Settings
from app.services.evidence import heuristic_structure
from app.services.exa import SearchResult, exa_client, mock_search
from app.services.extraction import heuristic_extract
from app.services.patterns import detect_patterns
from app.services.query_builder import build_queries
from app.services.risk import assess

SCAM_TEXT = """Company: ABC Technologies
Internship: Software Development Intern
They contacted me on WhatsApp.
They said I was selected without an interview.
They offered Rs 40,000/month.
They said I have to pay Rs 1,500 registration fee.
They also said selected students will get a free laptop.
Website: abc-careers.xyz"""

LEGIT_TEXT = """Company: ADP
Role: GPT Intern (Technology)
Location: Hyderabad
Selection Process: Online Assessment, Technical Interviews, HR Discussion
Apply here: https://jobs.adp.com/en/jobs/ind169248/"""


def _empty_input(**overrides) -> UserInput:
    base = UserInput(
        company=CompanyInfo(name="Acme"),
        opportunity=OpportunityInfo(type="internship"),
    )
    return base.model_copy(update=overrides)


def test_neon_url_is_normalized_for_async_engine() -> None:
    neon = Settings(database_url="postgresql://u:p@ep-cool-123.us-east-2.aws.neon.tech/db")
    assert neon.sqlalchemy_database_url.startswith("postgresql+asyncpg://")
    assert neon.sqlalchemy_database_url.endswith("?ssl=require")
    assert neon.is_sqlite is False

    # A Neon URL copied with libpq params must be translated for asyncpg.
    neon_full = Settings(
        database_url=(
            "postgresql://u:p@ep-x.us-east-2.aws.neon.tech/db"
            "?sslmode=require&channel_binding=require"
        )
    )
    normalized = neon_full.sqlalchemy_database_url
    assert "ssl=require" in normalized
    assert "sslmode" not in normalized
    assert "channel_binding" not in normalized

    local = Settings(database_url="sqlite+aiosqlite:///./data/x.db")
    assert local.sqlalchemy_database_url == local.database_url
    assert local.is_sqlite is True


def test_mock_search_is_labelled_and_deterministic() -> None:
    user_input = heuristic_extract(SCAM_TEXT)
    groups = build_queries(user_input)
    first = mock_search(groups, user_input)
    second = mock_search(groups, user_input)
    assert first
    assert all(r.is_mock and r.source_domain == "mock.freebuff.dev" for r in first)
    assert [r.url for r in first] == [r.url for r in second]


@pytest.mark.asyncio
async def test_exa_falls_back_to_mock_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exa_client, "enabled", False)
    user_input = heuristic_extract(SCAM_TEXT)
    groups = build_queries(user_input)
    bundle = await exa_client.search_many(groups, user_input)
    assert bundle.used_mock is True
    assert bundle.results
    assert all(r.is_mock for r in bundle.results)


def test_off_topic_scam_pages_do_not_incriminate_the_company() -> None:
    results = [
        SearchResult(
            query="ADP scam",
            category="scam_complaints",
            title="Beware of online job scams",
            url="https://example.com/scams",
            snippet="Scammers ask for a registration fee and contact people on WhatsApp.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP payment",
            category="payment_complaints",
            title="Job fraud alert",
            url="https://example.com/alert",
            snippet="Never pay a registration fee to a recruiter over WhatsApp.",
            source_domain="example.com",
        ),
    ]
    reviews, _evidence, _findings = heuristic_structure(results, heuristic_extract(LEGIT_TEXT))
    assert reviews.negative_mentions == 0
    assert reviews.payment_complaints == 0
    assert reviews.whatsapp_complaints == 0


def test_impersonation_pages_do_not_count_against_the_company() -> None:
    """Pages describing the brand being impersonated show it as the victim."""
    results = [
        SearchResult(
            query="ADP phishing",
            category="scam_complaints",
            title="Scammers are impersonating ADP",
            url="https://example.com/adp-phishing",
            snippet=(
                "Fraudsters impersonating ADP are sending phishing emails and asking "
                "candidates for a registration fee over WhatsApp."
            ),
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP fake recruiters",
            category="payment_complaints",
            title="Fake recruiters posing as ADP",
            url="https://example.com/adp-fake",
            snippet="Someone is using the name of ADP to demand an upfront fee.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP imposter",
            category="scam_complaints",
            title="ADP Imposter",
            url="https://example.com/adp-imposter",
            snippet="Scam type: ADP Imposter. A scammer sent a fake payroll job offer.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP scam newspaper",
            category="job_complaints",
            title="On getting scammed",
            url="https://example.com/adp-story",
            snippet=(
                'A "hiring manager" who called himself "John Muller" from "ADP" '
                "texted me about a job — it turned out to be a scam."
            ),
            source_domain="example.com",
        ),
    ]
    reviews, evidence, _findings = heuristic_structure(results, heuristic_extract(LEGIT_TEXT))
    assert reviews.negative_mentions == 0
    assert reviews.payment_complaints == 0
    assert reviews.whatsapp_complaints == 0
    assert evidence == []


def test_ordinary_hr_complaint_is_not_a_fraud_signal() -> None:
    """A general workplace gripe is not evidence of a fake job."""
    results = [
        SearchResult(
            query="ADP reviews",
            category="company_reviews",
            title="Working At ADP - Ask a Question",
            url="https://example.com/adp-reviews",
            snippet=(
                "My only complaint with ADP is our strict 3x2 hybrid model. "
                "Otherwise I love this company."
            ),
            source_domain="example.com",
        )
    ]
    reviews, _evidence, _findings = heuristic_structure(results, heuristic_extract(LEGIT_TEXT))
    assert reviews.negative_mentions == 0


def test_negative_reputation_requires_corroboration() -> None:
    """Two isolated mentions are not enough; a real pattern needs volume."""
    user_input = _empty_input()
    two = detect_patterns(
        user_input,
        DomainIntel(),
        ReviewSignals(total_mentions=10, negative_mentions=2),
        sources_checked=10,
    )
    three = detect_patterns(
        user_input,
        DomainIntel(),
        ReviewSignals(total_mentions=10, negative_mentions=3),
        sources_checked=10,
    )
    assert "negative_reputation" not in {p.pattern for p in two}
    assert "negative_reputation" in {p.pattern for p in three}


def test_negative_reputation_requires_distinct_sources() -> None:
    """Volume from a single site is not independent corroboration."""
    user_input = _empty_input()
    one_source = detect_patterns(
        user_input,
        DomainIntel(),
        ReviewSignals(
            total_mentions=10,
            negative_mentions=4,
            negative_source_domains=["forum.example.com"],
        ),
        sources_checked=10,
    )
    two_sources = detect_patterns(
        user_input,
        DomainIntel(),
        ReviewSignals(
            total_mentions=10,
            negative_mentions=4,
            negative_source_domains=["forum.example.com", "news.example.org"],
        ),
        sources_checked=10,
    )
    assert "negative_reputation" not in {p.pattern for p in one_source}
    assert "negative_reputation" in {p.pattern for p in two_sources}


def test_adp_impersonation_reports_do_not_raise_risk() -> None:
    """End-to-end guard: ADP-style pages (victim, HR gripes) stay LOW 0.

    This pins the false-positive fix so future pattern/threshold tweaks cannot
    silently push a reputable, impersonated company back up the risk scale.
    """
    company = "ADP"
    results = [
        SearchResult(
            query="ADP scam",
            category="scam_complaints",
            title="Beware of online job scams",
            url="https://example.com/advice",
            snippet="Scammers ask for a registration fee and contact people on WhatsApp.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP imposter",
            category="scam_complaints",
            title="ADP Imposter",
            url="https://example.com/adp-imposter",
            snippet="Scam type: ADP Imposter. A scammer sent a fake payroll job offer.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP phishing",
            category="payment_complaints",
            title="Scammers are impersonating ADP",
            url="https://example.com/adp-phishing",
            snippet="Fraudsters posing as ADP demanded an upfront registration fee over WhatsApp.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP reviews",
            category="company_reviews",
            title=f"Working At {company}",
            url="https://example.com/adp-reviews",
            snippet="My only complaint with ADP is our strict hybrid model. Otherwise I love it.",
            source_domain="example.com",
        ),
        SearchResult(
            query="ADP careers",
            category="domain_mentions",
            title="ADP Careers",
            url="https://jobs.adp.com/en/jobs/ind169248/",
            snippet="Apply through the official ADP careers portal.",
            source_domain="jobs.adp.com",
        ),
    ]
    user_input = heuristic_extract(LEGIT_TEXT)
    reviews, evidence, _findings = heuristic_structure(results, user_input)

    assert reviews.negative_mentions == 0
    assert reviews.payment_complaints == 0
    assert reviews.whatsapp_complaints == 0
    assert reviews.negative_source_domains == []
    assert evidence == []

    patterns = detect_patterns(
        user_input,
        DomainIntel(
            domain="jobs.adp.com",
            tld="com",
            company_name_match=True,
            age_days=9000,
        ),
        reviews,
        sources_checked=reviews.total_mentions,
    )
    risk = assess(
        patterns,
        user_input=user_input,
        evidence=evidence,
        sources_checked=reviews.total_mentions,
        used_mock=False,
    )
    assert patterns == []
    assert risk.score == 0
    assert risk.level == "LOW"


def test_on_topic_complaints_still_count() -> None:
    results = [
        SearchResult(
            query="ADP complaints",
            category="scam_complaints",
            title="ADP complaints",
            url="https://example.com/adp",
            snippet="ADP scam reports: recruiters asked for a registration fee over WhatsApp.",
            source_domain="example.com",
        )
    ]
    reviews, _evidence, _findings = heuristic_structure(results, heuristic_extract(LEGIT_TEXT))
    assert reviews.negative_mentions >= 1
    assert reviews.payment_complaints >= 1
    assert reviews.whatsapp_complaints >= 1


def test_heuristic_extracts_contacts_and_money() -> None:
    result = heuristic_extract(SCAM_TEXT)
    assert result.company.name == "ABC Technologies"
    assert "abc-careers.xyz" in result.contacts.domains
    assert result.communication.whatsapp is True
    assert result.money_request.detected is True
    assert result.money_request.amount is not None


def test_heuristic_does_not_invent_fees_for_legit_text() -> None:
    result = heuristic_extract(LEGIT_TEXT)
    assert result.money_request.detected is False
    assert result.communication.whatsapp is False
    assert "jobs.adp.com" in result.contacts.domains


def test_scam_input_is_high_risk() -> None:
    user_input = heuristic_extract(SCAM_TEXT)
    patterns = detect_patterns(
        user_input,
        DomainIntel(domain="abc-careers.xyz", tld="xyz", cheap_tld=True, age_days=20),
        ReviewSignals(total_mentions=10, negative_mentions=4, payment_complaints=3),
        historical_reports=0,
        sources_checked=10,
    )
    ids = {p.pattern for p in patterns}
    assert "upfront_payment" in ids
    assert "suspicious_reward_offer" in ids
    assert "no_interview_selection" in ids
    assert "recent_domain" in ids

    risk = assess(
        patterns,
        user_input=user_input,
        evidence=[Evidence(type="complaint", summary="asked for fee")],
        sources_checked=10,
        used_mock=True,
    )
    assert risk.level in {"HIGH", "CRITICAL"}
    assert risk.score >= 45


def test_clean_input_is_low_risk() -> None:
    user_input = heuristic_extract(LEGIT_TEXT)
    patterns = detect_patterns(
        user_input,
        DomainIntel(domain="jobs.adp.com", tld="com", company_name_match=True, age_days=9000),
        ReviewSignals(total_mentions=8),
        historical_reports=0,
        sources_checked=8,
    )
    assert patterns == []

    risk = assess(
        patterns,
        user_input=user_input,
        evidence=[],
        sources_checked=8,
        used_mock=False,
    )
    assert risk.level == "LOW"
    assert risk.score == 0


def test_cheap_tld_alone_is_not_enough() -> None:
    """A weak signal must not, by itself, produce a high score."""
    user_input = _empty_input(
        company=CompanyInfo(name="Acme", website="https://acme.xyz"),
    )
    patterns = detect_patterns(
        user_input,
        DomainIntel(domain="acme.xyz", tld="xyz", cheap_tld=True, age_days=4000),
        ReviewSignals(total_mentions=5),
        sources_checked=5,
    )
    risk = assess(patterns, user_input=user_input, evidence=[], sources_checked=5, used_mock=True)
    assert risk.level in {"LOW", "MODERATE"}
    assert risk.score < 45


def test_whatsapp_recruitment_alone_is_not_high_risk() -> None:
    """Messaging-app contact is common in legitimate Indian hiring too.

    On its own it must never yield a HIGH/CRITICAL verdict — only a concrete
    fraud signal (a fee, a data request, corroborated reports) can do that.
    """
    user_input = _empty_input(
        company=CompanyInfo(name="Adyapan"),
        communication=CommunicationChannels(whatsapp=True, telegram=True, email=True),
    )
    patterns = detect_patterns(
        user_input,
        DomainIntel(),
        ReviewSignals(total_mentions=6, whatsapp_complaints=2),
        sources_checked=6,
    )
    assert "whatsapp_recruitment" in {p.pattern for p in patterns}

    risk = assess(patterns, user_input=user_input, evidence=[], sources_checked=6, used_mock=False)
    assert risk.level in {"LOW", "MODERATE"}, risk.level
    assert risk.score < 45


def test_payment_demand_keeps_the_verdict_high() -> None:
    """The hard signal that should still drive a HIGH verdict."""
    user_input = _empty_input(
        company=CompanyInfo(name="Adyapan"),
        communication=CommunicationChannels(whatsapp=True),
        money_request=MoneyRequest(detected=True, amount="Rs 1,500", reason="registration fee"),
    )
    patterns = detect_patterns(
        user_input, DomainIntel(), ReviewSignals(total_mentions=6), sources_checked=6
    )
    risk = assess(patterns, user_input=user_input, evidence=[], sources_checked=6, used_mock=False)
    assert risk.level in {"HIGH", "CRITICAL"}, risk.level
    assert risk.score >= 45


def test_monthly_high_salary_for_intern_is_flagged() -> None:
    user_input = _empty_input(
        opportunity=OpportunityInfo(type="internship", salary="₹80,000/month"),
        communication=CommunicationChannels(),
        money_request=MoneyRequest(),
    )
    patterns = detect_patterns(
        user_input, DomainIntel(), ReviewSignals(total_mentions=3), sources_checked=3
    )
    assert "unusually_high_salary" in {p.pattern for p in patterns}


def test_responsible_salary_for_intern_is_not_flagged() -> None:
    user_input = _empty_input(opportunity=OpportunityInfo(type="internship", salary="₹15,000/month"))
    patterns = detect_patterns(user_input, DomainIntel(), ReviewSignals(total_mentions=3), sources_checked=3)
    assert "unusually_high_salary" not in {p.pattern for p in patterns}


def test_historical_reports_scale_with_volume() -> None:
    user_input = _empty_input()
    one = detect_patterns(user_input, DomainIntel(), ReviewSignals(), historical_reports=1)
    many = detect_patterns(user_input, DomainIntel(), ReviewSignals(), historical_reports=6)
    one_sev = {p.pattern: p.severity for p in one}
    many_sev = {p.pattern: p.severity for p in many}
    assert one_sev["historical_reports"] == "low"
    assert many_sev["historical_reports"] == "high"


def test_analyst_report_is_parsed_and_clamped() -> None:
    from app.services.evidence import _parse_analyst

    report = _parse_analyst(
        {
            "fraud_score": 150,
            "verdict": "likely_genuine",
            "summary": "looks real",
            "red_flags": [{"point": "registration fee", "evidence": "asked Rs 1,500"}],
            "green_flags": ["official careers domain"],
            "what_to_do": ["verify via the official site"],
            "sources_used": ["https://x.example/a"],
        }
    )
    assert report is not None
    assert report.fraud_score == 100  # clamped to range
    assert report.red_flags[0].point == "registration fee"
    assert report.green_flags[0].point == "official careers domain"
    assert report.what_to_do == ["verify via the official site"]


def test_negative_reputation_scales_with_corroboration() -> None:
    """Independent complaint sources must weigh more than an isolated mention.

    Dissatisfaction tops out inside MODERATE by design. Only explicit fraud
    accusations (``fraud_accusations``) may reach HIGH — see
    ``test_fraud_accusations_separate_from_employee_complaints``.
    """
    user_input = _empty_input()

    def reputation(mentions: int, domains: list[str]):
        reviews = ReviewSignals(
            total_mentions=20,
            negative_mentions=mentions,
            negative_source_domains=domains,
        )
        patterns = {
            p.pattern: p
            for p in detect_patterns(
                user_input, DomainIntel(), reviews, sources_checked=20
            )
        }
        return patterns.get("negative_reputation")

    assert reputation(2, ["a.example"]) is None  # below the volume floor
    assert reputation(3, ["a.example"]) is None  # one site is not corroboration

    unknown_spread = reputation(5, [])  # volume reported, spread not
    assert unknown_spread is not None and unknown_spread.severity == "medium"

    many_sources = reputation(5, ["a.example", "b.example"])
    assert many_sources is not None and many_sources.severity == "medium"
    assert many_sources.confidence > unknown_spread.confidence

    # Corroborated *accusations* are the thing that can reach HIGH.
    reviews = ReviewSignals(
        total_mentions=20,
        negative_mentions=5,
        fraud_accusations=3,
        negative_source_domains=["a.example", "b.example"],
    )
    patterns = detect_patterns(user_input, DomainIntel(), reviews, sources_checked=20)
    risk = assess(patterns, user_input=user_input, evidence=[], sources_checked=20, used_mock=False)
    assert risk.level in {"HIGH", "CRITICAL"}


def _trusted_input() -> UserInput:
    """A posting that looks like it genuinely comes from the employer."""
    return heuristic_extract(
        "Company: Acme\nApply here: https://acme.com/careers\nContact: hr@acme.com"
    )


def _serious_signal(pattern: str = "repeated_payment_complaints") -> DetectedPattern:
    return DetectedPattern(
        pattern=pattern,
        category="reputation",
        detected=True,
        severity="critical",
        confidence=0.85,
        rationale="independent sources describe fee demands",
    )


def test_strong_trust_plus_serious_signal_reports_a_conflict() -> None:
    """Trust says "real employer", a serious signal says "fraud" — say both."""
    risk = assess(
        [_serious_signal()],
        user_input=_trusted_input(),
        evidence=[],
        sources_checked=10,
        used_mock=False,
        domain=DomainIntel(domain="acme.com", company_name_match=True, dns_resolves=True),
        reviews=ReviewSignals(total_mentions=10, negative_mentions=4, payment_complaints=3),
    )
    assert risk.trust_score >= 60
    assert risk.status == "CONFLICTING_EVIDENCE"
    # The conflict reports the disagreement; it must not soften the fraud signal.
    assert risk.level in {"HIGH", "CRITICAL"}
    assert "conflict" in risk.headline.lower()


def test_conclusive_fraud_still_beats_the_conflict_state() -> None:
    """A posting that itself demands payment stays conclusively fraudulent."""
    pattern = DetectedPattern(
        pattern="upfront_payment",
        category="money",
        detected=True,
        severity="critical",
        confidence=0.95,
        rationale="asked for a registration fee",
    )
    risk = assess(
        [pattern],
        user_input=_trusted_input(),
        evidence=[],
        sources_checked=10,
        used_mock=False,
        domain=DomainIntel(domain="acme.com", company_name_match=True, dns_resolves=True),
    )
    assert risk.status == "FRAUDULENT_VERIFIED"


def test_weak_signal_does_not_manufacture_a_conflict() -> None:
    """A lone weak flag (one prior report) is not a conflict on its own."""
    pattern = DetectedPattern(
        pattern="historical_reports",
        category="history",
        detected=True,
        severity="low",
        confidence=0.52,
        rationale="one prior report",
    )
    risk = assess(
        [pattern],
        user_input=_trusted_input(),
        evidence=[],
        sources_checked=10,
        used_mock=False,
        domain=DomainIntel(domain="acme.com", company_name_match=True, dns_resolves=True),
    )
    assert risk.status != "CONFLICTING_EVIDENCE"


def test_serious_signal_without_strong_trust_is_not_a_conflict() -> None:
    user_input = heuristic_extract(
        "Company: Acme\nThey contacted me on WhatsApp and asked for a Rs 1,500 fee."
    )
    risk = assess(
        [_serious_signal()],
        user_input=user_input,
        evidence=[],
        sources_checked=10,
        used_mock=False,
    )
    assert risk.trust_score < 60
    assert risk.status != "CONFLICTING_EVIDENCE"


def test_strong_trust_alone_is_not_a_conflict() -> None:
    risk = assess(
        [],
        user_input=_trusted_input(),
        evidence=[],
        sources_checked=10,
        used_mock=False,
        domain=DomainIntel(domain="acme.com", company_name_match=True, dns_resolves=True),
        reviews=ReviewSignals(total_mentions=10),
    )
    assert risk.status != "CONFLICTING_EVIDENCE"


def test_legal_entity_company_name_is_extracted() -> None:
    """A title line like "Levroxen LLC - ..." must yield the company name."""
    user_input = heuristic_extract(
        "Levroxen LLC - Online Assessment Registration (Important)\n"
        "Assessment Platform: https://www.exametryx.com"
    )
    assert user_input.company.name == "Levroxen LLC"


def test_company_name_when_fields_share_one_line() -> None:
    """A single-line posting must yield the company name, not the sentence.

    Regression: the capture after "Company:" ran to the end of the line, so the
    README's one-line example produced "ABC Technologies. Selected on WhatsApp
    without interview. Pay Rs 1,500 registration fee. Website:
    abc-careers.xyz" as the company name. That name keys the topic filter, the
    search queries and the stored company record, so the whole sentence leaking
    in misdirects the investigation itself.
    """
    sentence = heuristic_extract(
        "Company: ABC Technologies. Selected on WhatsApp without interview. "
        "Pay Rs 1,500 registration fee. Website: abc-careers.xyz"
    )
    assert sentence.company.name == "ABC Technologies"

    # The same, with the fields separated by labels rather than full stops.
    labelled = heuristic_extract(
        "Company: ABC Technologies Role: AI Intern Website: abc-careers.xyz"
    )
    assert labelled.company.name == "ABC Technologies"

    # No punctuation at all: the money marker still ends the name.
    unpunctuated = heuristic_extract(
        "Company: ABC Technologies Pay Rs 1,500 Registration Fee"
    )
    assert unpunctuated.company.name == "ABC Technologies"

    # A dotted legal name is not a sentence boundary.
    dotted = heuristic_extract("Company: Acme Pvt. Ltd.\nRole: Analyst")
    assert dotted.company.name == "Acme Pvt. Ltd."

    # A sentence that follows a legal suffix still ends the name, and the
    # suffix keeps its dot.
    after_suffix = heuristic_extract(
        "Company: Cache Serve Ltd. Please review this posting."
    )
    assert after_suffix.company.name == "Cache Serve Ltd."

    # A name that merely ends in a cut word is left alone: no boundary is hit.
    assert heuristic_extract("Company: Samsung Pay").company.name == "Samsung Pay"

    # The multi-line shape keeps working unchanged.
    multi_line = heuristic_extract(
        "Company: ABC Technologies\n"
        "They asked for a Rs 1,500 registration fee. Website: abc-careers.xyz"
    )
    assert multi_line.company.name == "ABC Technologies"


def test_company_pages_are_not_filtered_out_by_a_platform_domain() -> None:
    """The topic filter must key on the company name, not a linked platform.

    Regression for the Levroxen miss: the post's only link was an assessment
    platform, so filtering on that domain discarded every real scam report.
    """
    user_input = heuristic_extract(
        "Levroxen LLC - Online Assessment Registration (Important)\n"
        "Assessment Platform: https://www.exametryx.com"
    )
    results = [
        SearchResult(
            query="Levroxen scam",
            category="scam_complaints",
            title="Levroxen scam",
            url=f"https://report{i}.example/x",
            snippet=(
                "Levroxen LLC reported as a scam. 100% fraudulent business, "
                "fake address, fake phone numbers."
            ),
            source_domain=f"report{i}.example",
        )
        for i in range(3)
    ]
    reviews, _evidence, _findings = heuristic_structure(results, user_input)
    assert reviews.negative_mentions == 3
    # "reported as a scam", "fraudulent business" are accusations about the
    # company, not merely dissatisfaction.
    assert reviews.fraud_accusations == 3
    patterns = detect_patterns(
        user_input, DomainIntel(), reviews, sources_checked=reviews.total_mentions
    )
    accusations = [p for p in patterns if p.pattern == "fraud_accusations_against_company"]
    assert accusations, "real scam reports must not be filtered out"
    assert accusations[0].severity == "critical"


@pytest.mark.asyncio
async def test_shortener_links_are_not_treated_as_the_company_domain() -> None:
    """A shortened apply link must not produce a domain-name mismatch.

    This is the eej.at false positive: a legit post that hides its link behind a
    shortener must not be read as "domain does not match the company".
    """
    from app.services.domain_check import check_domain

    user_input = heuristic_extract(
        "Company: Myntra\nRegistration Link: https://eej.at/jCUF0fi2"
    )
    intel = await check_domain(user_input)
    assert intel.domain != "eej.at"
    assert intel.company_name_match is not False


@pytest.mark.asyncio
async def test_structure_evidence_returns_the_analyst_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search-then-reason flow: Groq reads the pages and returns a rating."""
    from app.services import evidence as ev

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "reviews": {"total_mentions": 3, "negative_mentions": 2, "payment_complaints": 2},
            "evidence": [
                {
                    "type": "complaint",
                    "source_url": "https://forum.example/abc",
                    "source_domain": "forum.example",
                    "title": "Is ABC a scam?",
                    "summary": "asked for a registration fee",
                }
            ],
            "notable_findings": ["2 sources mention fees"],
            "analyst": {
                "fraud_score": 82,
                "verdict": "fraudulent",
                "summary": "Multiple sources describe a fee demand.",
                "red_flags": [{"point": "upfront fee", "evidence": "asked Rs 1,500"}],
                "green_flags": [],
                "what_to_do": ["Do not pay"],
                "sources_used": ["https://forum.example/abc"],
            },
        }

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    results = [
        SearchResult(
            query="q",
            category="scam_complaints",
            title="Is ABC a scam?",
            url="https://forum.example/abc",
            snippet="asked for a registration fee",
            source_domain="forum.example",
        )
    ]
    reviews, evidence, findings, analyst, coverage = await ev.structure_evidence(
        results, heuristic_extract(SCAM_TEXT)
    )
    assert reviews.total_mentions == 3
    assert coverage.pages_captured == 1
    assert coverage.pages_in_prompt == 1
    assert analyst is not None
    assert analyst.fraud_score == 82
    assert analyst.verdict == "fraudulent"
    assert analyst.red_flags[0].point == "upfront fee"
