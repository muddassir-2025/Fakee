"""Evidence-selection tests over the labelled captured-page scenarios.

These guard the step that turns captured pages into the Groq prompt. The failure
mode they pin down is *positional truncation*: when more pages are captured than
the per-investigation character budget allows, a naive first-N selection drops
whichever pages happened to be captured last — which is how a scam complaint can
silently disappear and the company read as clean. Selection must be by
relevance, with near-duplicate pages collapsed so the budget reaches distinct
evidence.

The scenario labels are the project's own review of the fixture, not a claim of
measured accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.services.evidence import _render_results
from app.services.exa import SearchResult
from app.services.extraction import heuristic_extract

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "captured_pages.json").read_text(encoding="utf-8")
)
_BOILERPLATE = _FIXTURE["page_boilerplate"]
SCENARIOS = _FIXTURE["scenarios"]
IDS = [s["id"] for s in SCENARIOS]


def _results(scenario: dict) -> list[SearchResult]:
    """Build captured pages the way the scraper delivers them.

    Real captures arrive with the readable text followed by navigation, cookie
    and footer noise; each page is well over the per-result cap. That is what
    makes the total exceed the budget in the first place.
    """
    return [
        SearchResult(
            query="",
            category=p["category"],
            title=p["title"],
            url=p["url"],
            snippet=f"{p['snippet']}\n{_BOILERPLATE}",
            source_domain=p["source_domain"],
        )
        for p in scenario["pages"]
    ]


def _render(scenario: dict) -> str:
    return _render_results(_results(scenario), heuristic_extract(scenario["intro_text"]))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_relevant_pages_survive_the_budget(scenario: dict) -> None:
    """The pages the scenario marks as decisive must reach the prompt."""
    rendered = _render(scenario)
    for url in scenario["expect"]["must_include_urls"]:
        assert url in rendered, (
            f"{scenario['id']}: decisive page {url} was dropped from the prompt"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_duplicate_pages_are_collapsed(scenario: dict) -> None:
    """Republished mirrors must not crowd out distinct evidence."""
    rendered = _render(scenario)
    for group in scenario["expect"].get("dedupe_groups", []):
        present = [url for url in group if url in rendered]
        assert len(present) <= 1, (
            f"{scenario['id']}: near-duplicate pages were not collapsed: {present}"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_prompt_respects_the_character_budget(scenario: dict) -> None:
    """The rendered prompt must never exceed the configured evidence budget."""
    rendered = _render(scenario)
    assert len(rendered) <= settings.groq_evidence_max_chars, (
        f"{scenario['id']}: prompt is {len(rendered)} chars, "
        f"budget is {settings.groq_evidence_max_chars}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_selection_is_deterministic(scenario: dict) -> None:
    """The same captured set must render identically — the verdict is reproducible."""
    assert _render(scenario) == _render(scenario)


def _page(category: str, title: str, url: str, snippet: str, domain: str) -> SearchResult:
    return SearchResult(
        query="q",
        category=category,
        title=title,
        url=url,
        snippet=snippet,
        source_domain=domain,
    )


# The generic pages a run like the reported one actually captured: the searches
# "<employer> <claim>" and "<employer> telegram job offer" return backlog
# explainers and Telegram job boards rather than anything about the employer.
GENERIC_PAGES = [
    _page(
        "specific_claims",
        "What is backlog university",
        "https://openeducat.org/articles/what-is-backlog-university/",
        "A backlog is a subject you must clear. " * 40,
        "openeducat.org",
    ),
    _page(
        "specific_claims",
        "What is no active backlogs? - Brainly.in",
        "https://brainly.in/question/57331822",
        "What is the meaning of no active backlogs? " * 40,
        "brainly.in",
    ),
    _page(
        "channel_complaints",
        "Telegram Jobs (NOW HIRING)",
        "https://www.ziprecruiter.com/Jobs/Telegram",
        "Drive outreach via Telegram BD communities. " * 40,
        "ziprecruiter.com",
    ),
]


def _employer_pages() -> list[SearchResult]:
    return [
        _page(
            "company_reviews",
            f"Infosys page {index}",
            f"https://infosys-site-{index}.example/infosys",
            "Infosys Limited is an Indian multinational IT company. " * 40,
            f"infosys-site-{index}.example",
        )
        for index in range(3)
    ]


def test_generic_pages_are_not_offered_as_evidence() -> None:
    """A page that never names the company must not reach the model as evidence.

    Ranking put the generic pages last but the character budget still had room
    for them, so they reached the model as if they were evidence about the
    employer, the analyst listed them among the URLs it relied on, and the panel
    displayed a "what is a backlog" explainer as a source for that employer.
    """
    user_input = heuristic_extract("Company: Infosys\nRole: Internship\nNo active backlogs")
    rendered = _render_results([*GENERIC_PAGES, *_employer_pages()], user_input)

    for page in _employer_pages():
        assert page.url in rendered, f"the employer's own page {page.url} was dropped"
    for page in GENERIC_PAGES:
        assert page.url not in rendered, f"generic page {page.url} was offered as evidence"


def test_generic_pages_are_still_shown_when_no_page_names_the_company() -> None:
    """With nothing on-topic captured, the generic pages are all there is.

    Excluding them there would leave the model an empty prompt, and "these
    results never mention the company" is the reading worth having.
    """
    rendered = _render_results(GENERIC_PAGES, heuristic_extract("Company: Infosys"))
    assert GENERIC_PAGES[0].url in rendered


def test_all_pages_are_kept_when_they_fit() -> None:
    """Selection must not drop anything when the whole set is within budget."""
    results = [
        SearchResult(
            query="q",
            category="scam_complaints",
            title=f"Page {i}",
            url=f"https://example-{i}.test/p",
            snippet=f"Distinct content number {i}.",
            source_domain=f"example-{i}.test",
        )
        for i in range(3)
    ]
    rendered = _render_results(results, heuristic_extract("Company: Acme"))
    for i in range(3):
        assert f"https://example-{i}.test/p" in rendered
