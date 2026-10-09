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
