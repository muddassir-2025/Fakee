"""Reconciliation, coverage, the inconclusive safeguard, and the cost skips.

These pin the behaviour that makes the verdict trustworthy when evidence is
partial: rule findings for pages the model never saw must still count, the
model's own counts must not be blindly overridden, the result must be able to
say "insufficient evidence" instead of "safe", and the expensive model calls
must be skipped only where that is provably safe.
"""

from __future__ import annotations

import pytest

from app.schemas import (
    DetectedPattern,
    DomainIntel,
    Evidence,
    EvidenceCoverage,
    ReviewSignals,
    UserInput,
    CompanyInfo,
    OpportunityInfo,
)
from app.services import evidence as ev
from app.services import extraction as ex
from app.services.evidence import _merge_reviews, has_topical_evidence
from app.services.exa import SearchResult
from app.services.risk import assess

# A long, repeated sentence so each captured page contributes several hundred
# characters of *signal* — that is what pushes pages out of the prompt budget.
SIGNAL_SENTENCE = (
    "Acme is a scam: they demanded a registration fee over WhatsApp before "
    "onboarding and cheated applicants, several of whom were not paid at all. "
)

COMPANY_TEXT = "Company: Acme\nThey asked me to pay a Rs 1,500 registration fee on WhatsApp."


def _page(index: int, *, title: str | None = None, sentences: int = 6) -> SearchResult:
    return SearchResult(
        query="",
        category="scam_complaints",
        title=title or f"Acme complaint number {index}",
        url=f"https://forum{index}.example/acme-{index}",
        snippet=(SIGNAL_SENTENCE * sentences).strip(),
        source_domain=f"forum{index}.example",
    )


def _user_input() -> UserInput:
    return ex.heuristic_extract(COMPANY_TEXT)


# --------------------------------------------------------------------------
# C — always-on deterministic analysis and careful reconciliation
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rule_findings_from_unseen_pages_are_folded_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence on pages the model never saw must still count.

    The model is made to report only what it could see (4 pages); the remaining
    pages are analysed by the rules and their complaints must survive.
    """
    seen_count = 4

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        # Mimic a model that only counted the pages present in its prompt.
        return {
            "reviews": {
                "total_mentions": seen_count,
                "negative_mentions": 2,
                "payment_complaints": 1,
            },
            "evidence": [],
            "notable_findings": [],
            "analyst": None,
        }

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    results = [_page(i) for i in range(12)]
    reviews, _evidence, _findings, _analyst, coverage = await ev.structure_evidence(
        results, _user_input()
    )

    # The prompt could not hold everything...
    assert coverage.pages_in_prompt < coverage.pages_captured
    assert coverage.pages_rules_only > 0
    # ...but the dropped pages were still analysed, so their complaints count.
    assert reviews.negative_mentions > 2
    assert reviews.payment_complaints > 1
    assert reviews.total_mentions > seen_count


@pytest.mark.asyncio
async def test_model_counts_are_not_blindly_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model reads context the rules cannot, so its counts are not overridden.

    A single page that the rules read as neutral must not have the model's
    higher count clamped down to the rule-engine value.
    """

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "reviews": {"total_mentions": 10, "negative_mentions": 7},
            "evidence": [
                {
                    "type": "complaint",
                    "source_url": "https://forum.example/acme",
                    "summary": "fee demand",
                }
            ],
            "notable_findings": [],
        }

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    # One short, apparently innocuous page: the rules alone would find nothing.
    results = [
        SearchResult(
            query="",
            category="company_reviews",
            title="Acme overview",
            url="https://forum.example/acme",
            snippet="Acme is a company. Nothing else notable was found here.",
            source_domain="forum.example",
        )
    ]
    reviews, _evidence, _findings, _analyst, _coverage = await ev.structure_evidence(
        results, _user_input()
    )
    assert reviews.total_mentions == 10
    assert reviews.negative_mentions == 7


def test_merge_reviews_is_additive_and_keeps_the_invariant() -> None:
    primary = ReviewSignals(
        total_mentions=2,
        negative_mentions=2,
        negative_source_domains=["a.example"],
        source_urls=["https://a.example/x"],
    )
    extra = ReviewSignals(
        total_mentions=3,
        negative_mentions=3,
        negative_source_domains=["b.example", "a.example"],
        source_urls=["https://b.example/y"],
    )
    merged = _merge_reviews(primary, extra)
    assert merged.total_mentions == 5
    assert merged.negative_mentions == 5
    assert merged.negative_source_domains == ["a.example", "b.example"]
    assert merged.source_urls == ["https://a.example/x", "https://b.example/y"]
    assert merged.negative_mentions <= merged.total_mentions


# --------------------------------------------------------------------------
# C1 — a page that never names the company is background, not support
# --------------------------------------------------------------------------
def test_evidence_reports_whether_the_page_names_the_company() -> None:
    """Rule-derived evidence carries whether the page is about the company.

    A page that never names the employer cannot be support for a verdict about
    them, so the item says so and the report labels it. This is reachable when a
    provider states the sentiment itself: the page is kept as negative evidence,
    but it is background all the same.
    """
    stated_negative = SearchResult(
        query="Acme scam",
        category="scam_complaints",
        title="Beware of online job scams",
        url="https://advice.example/jobs",
        snippet="Scammers ask candidates for a registration fee over WhatsApp.",
        source_domain="advice.example",
        sentiment="negative",
    )
    _reviews, evidence, _findings = ev.heuristic_structure(
        [stated_negative, _page(0)], _user_input()
    )

    by_url = {item.source_url: item for item in evidence}
    assert by_url["https://advice.example/jobs"].topical is False
    assert by_url["https://forum0.example/acme-0"].topical is True


def test_a_page_that_never_names_the_company_is_not_evidence_at_all() -> None:
    """Keywords alone cannot make a generic page evidence about the employer.

    Captured pages carry no sentiment, so a generic scam-awareness page is not
    negative and never enters the list — the rule that keeps it out is what the
    label exists as a backstop for.
    """
    generic = SearchResult(
        query="Acme scam",
        category="scam_complaints",
        title="Beware of online job scams",
        url="https://advice.example/jobs",
        snippet="Scammers ask candidates for a registration fee over WhatsApp.",
        source_domain="advice.example",
    )
    _reviews, evidence, _findings = ev.heuristic_structure([generic, _page(0)], _user_input())

    urls = {item.source_url for item in evidence}
    assert "https://advice.example/jobs" not in urls
    assert "https://forum0.example/acme-0" in urls


# --------------------------------------------------------------------------
# C2 — the analyst's citations must be pages this run actually read
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_analyst_sources_are_limited_to_pages_this_run_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cited URL that was never fetched must not reach the report.

    The model reliably lists addresses it was never given — a stale slug, a
    guessed URL — and the panel renders each one as a confident link, so an
    address that was never read appears as the source of the verdict. Only
    citations that resolve to a captured page survive, and they survive as the
    captured address rather than the model's spelling of it.
    """

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "reviews": {"total_mentions": 1, "negative_mentions": 0},
            "evidence": [],
            "notable_findings": [],
            "analyst": {
                "fraud_score": 10,
                "verdict": "needs_verification",
                "sources_used": [
                    "https://www.forum0.example/acme-0/#comments",  # captured, respelled
                    "https://invented.example/never-fetched",  # never fetched
                    "https://forum0.example/acme-0",  # captured, exact
                ],
            },
        }

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    results = [_page(0)]
    _r, _e, _f, analyst, _coverage = await ev.structure_evidence(results, _user_input())

    assert analyst is not None
    assert analyst.sources_used == ["https://forum0.example/acme-0"]


@pytest.mark.asyncio
async def test_evidence_items_cite_only_pages_this_run_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evidence list must not link to a page that was never fetched.

    Evidence is shown with its URL as a link, so an invented address is displayed
    as the basis of the finding it is attached to. A citation that resolves to a
    captured page is kept under the captured address (which is also the key the
    extension labels the page's search provider with); one that does not is
    dropped, and the rule engine still contributes its own items for the pages
    the model never saw.
    """

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "reviews": {"total_mentions": 3, "negative_mentions": 1},
            "evidence": [
                {
                    "type": "complaint",
                    "source_url": "https://forum0.example/acme-0/",
                    "source_domain": "forum0.example",
                    "title": "Acme complaint",
                    "summary": "Acme demanded a registration fee.",
                    "relevance": 0.9,
                },
                {
                    "type": "complaint",
                    "source_url": "https://invented.example/never-fetched",
                    "source_domain": "invented.example",
                    "title": "Acme scam",
                    "summary": "Invented page.",
                    "relevance": 0.9,
                },
            ],
            "notable_findings": [],
        }

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    _r, evidence, _f, _a, _c = await ev.structure_evidence([_page(0)], _user_input())

    urls = [item.source_url for item in evidence]
    assert "https://forum0.example/acme-0" in urls
    assert "https://invented.example/never-fetched" not in urls


# --------------------------------------------------------------------------
# D — coverage reporting
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_coverage_reports_captured_and_prompted_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {"reviews": {"total_mentions": 3}, "evidence": [], "notable_findings": []}

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", fake_chat_json)

    results = [_page(i) for i in range(6)]
    _r, _e, _f, _a, coverage = await ev.structure_evidence(results, _user_input())

    assert coverage.pages_captured == 6
    assert coverage.pages_in_prompt + coverage.pages_rules_only == 6
    assert coverage.signal_sentences > 0
    assert coverage.topical is True
    assert coverage.sufficient is True
    assert coverage.note == ""


@pytest.mark.asyncio
async def test_coverage_is_insufficient_when_no_page_is_on_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("Groq must not be called with only off-topic pages")

    monkeypatch.setattr(ev.groq_client, "enabled", True)
    monkeypatch.setattr(ev.groq_client, "chat_json", never_called)

    off_topic = [
        SearchResult(
            query="Acme scam",
            category="scam_complaints",
            title="Beware of online job scams",
            url=f"https://advice{i}.example/jobs",
            snippet="Scammers ask for a registration fee over WhatsApp. No company is named.",
            source_domain=f"advice{i}.example",
        )
        for i in range(3)
    ]
    assert has_topical_evidence(off_topic, _user_input()) is False
    _r, _e, _f, _a, coverage = await ev.structure_evidence(off_topic, _user_input())
    assert coverage.sufficient is False
    assert coverage.topical is False
    assert coverage.pages_in_prompt == 0
    assert "inconclusive" in coverage.note


# --------------------------------------------------------------------------
# The "Insufficient evidence" safeguard
# --------------------------------------------------------------------------
def _no_signals() -> list[DetectedPattern]:
    return []


def _assess(patterns: list[DetectedPattern], coverage: EvidenceCoverage | None):
    return assess(
        patterns,
        user_input=ex.heuristic_extract(COMPANY_TEXT),
        evidence=[],
        sources_checked=0,
        used_mock=False,
        coverage=coverage,
    )


def test_unusable_coverage_yields_insufficient_evidence_not_safe() -> None:
    coverage = EvidenceCoverage(pages_captured=0, sufficient=False, topical=False)
    risk = _assess(_no_signals(), coverage)
    assert risk.status == "INSUFFICIENT_EVIDENCE"
    assert risk.status != "LEGITIMATE_VERIFIED"
    assert "evidence" in risk.headline.lower()
    assert risk.confidence <= 0.3


def test_no_coverage_and_no_sources_is_insufficient() -> None:
    risk = _assess(_no_signals(), None)
    assert risk.status == "INSUFFICIENT_EVIDENCE"


def test_hard_signal_prevents_the_insufficient_verdict() -> None:
    """A real detection must never be masked by an inconclusive-coverage flag."""
    pattern = DetectedPattern(
        pattern="recent_domain",
        category="domain",
        detected=True,
        severity="high",
        confidence=0.9,
        rationale="registered recently",
    )
    coverage = EvidenceCoverage(pages_captured=0, sufficient=False, topical=False)
    risk = _assess([pattern], coverage)
    assert risk.status != "INSUFFICIENT_EVIDENCE"


def test_conclusive_fraud_beats_insufficient_evidence() -> None:
    pattern = DetectedPattern(
        pattern="upfront_payment",
        category="money",
        detected=True,
        severity="critical",
        confidence=0.95,
        rationale="asked for a registration fee",
    )
    coverage = EvidenceCoverage(pages_captured=0, sufficient=False, topical=False)
    risk = _assess([pattern], coverage)
    assert risk.status == "FRAUDULENT_VERIFIED"


def test_sufficient_coverage_without_signals_is_not_insufficient() -> None:
    coverage = EvidenceCoverage(pages_captured=9, pages_in_prompt=9, sufficient=True, topical=True)
    risk = _assess(_no_signals(), coverage)
    assert risk.status != "INSUFFICIENT_EVIDENCE"


# --------------------------------------------------------------------------
# Cost skips
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extraction_skips_groq_when_heuristics_are_reliable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("Groq extraction should have been skipped")

    monkeypatch.setattr(ex.groq_client, "enabled", True)
    monkeypatch.setattr(ex.groq_client, "chat_json", never_called)

    result = await ex.extract_user_input(COMPANY_TEXT)
    assert result.extraction_source == "heuristic"
    assert result.company.name == "Acme"
    assert result.money_request.detected is True


@pytest.mark.asyncio
async def test_extraction_uses_groq_when_heuristics_are_weak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def counting_chat_json(system: str, user: str, **kwargs):
        calls.append(1)
        return {"company": {"name": "Mystery Corp"}}

    monkeypatch.setattr(ex.groq_client, "enabled", True)
    monkeypatch.setattr(ex.groq_client, "chat_json", counting_chat_json)

    # No company, no contact, no money, no claims: the heuristics have nothing
    # to stand on, so Groq is worth asking.
    result = await ex.extract_user_input("Please look into this opportunity for me.")
    assert calls == [1]
    assert result.company.name == "Mystery Corp"
