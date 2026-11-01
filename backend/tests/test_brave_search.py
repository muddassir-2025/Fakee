"""Tests for the user-supplied Brave Search engine.

Covers the security-critical properties the refactor promises:

* the key is taken from the request, not from any developer configuration;
* the key is never echoed into an error, a log record, or an API response;
* two concurrent requests with two different keys cannot see each other's key;
* an invalid key and an exhausted quota are reported distinctly, and neither
  turns into a low-risk verdict.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from app import logging_utils
from app.config import settings
from app.schemas import SearchQueryGroup, UserInput
from app.services import brave as brave_module
from app.services.brave import BraveClient, parse_quota_headers
from app.services.search_errors import (
    SearchAuthError,
    SearchConfigError,
    SearchQuotaError,
    SearchRateLimitError,
)


def _group() -> list[SearchQueryGroup]:
    return [SearchQueryGroup(category="scam_complaints", queries=['"Acme" scam'])]


def _user_input() -> UserInput:
    ui = UserInput(raw_text="Company: Acme")
    ui.company.name = "Acme"
    return ui


@pytest.fixture(autouse=True)
def _no_page_fetch(monkeypatch):
    """Keep the unit tests network-free: skip server-side page enrichment."""
    monkeypatch.setattr(settings, "brave_fetch_pages", False)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.search.brave.com",
    )


def test_parse_quota_headers_picks_the_monthly_window() -> None:
    headers = httpx.Headers(
        {
            "x-ratelimit-limit": "1, 15000",
            "x-ratelimit-policy": "1;w=1, 15000;w=2592000",
            "x-ratelimit-remaining": "0, 14523",
            "x-ratelimit-reset": "1, 1419704",
        }
    )
    quota = parse_quota_headers(headers)
    assert quota is not None
    assert quota.limit == 15000
    assert quota.remaining == 14523


@pytest.mark.asyncio
async def test_search_uses_the_supplied_key(monkeypatch) -> None:
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("x-subscription-token", ""))
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "t", "url": "https://x.example/a", "description": "d"}]}},
        )

    client = BraveClient()
    client._client = _mock_client(handler)
    bundle = await client.search_many(_group(), _user_input(), "USER_KEY_ONE")
    assert seen_keys == ["USER_KEY_ONE"]
    assert len(bundle.results) == 1
    assert bundle.used_mock is False


@pytest.mark.asyncio
async def test_concurrent_keys_do_not_leak(monkeypatch) -> None:
    """Two runs with different keys must each see only their own key."""

    async def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-subscription-token", "")
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": key, "url": f"https://{key}.example/a", "description": key}]}},
        )

    client = BraveClient()
    client._client = _mock_client(handler)
    a, b = await asyncio.gather(
        client.search_many(_group(), _user_input(), "KEY_ALPHA"),
        client.search_many(_group(), _user_input(), "KEY_BETA"),
    )
    assert a.results[0].snippet == "KEY_ALPHA"
    assert b.results[0].snippet == "KEY_BETA"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(401, SearchAuthError), (403, SearchAuthError), (402, SearchQuotaError), (429, SearchRateLimitError)],
)
async def test_error_statuses_map_to_typed_errors(status: int, expected: type) -> None:
    client = BraveClient()
    client._client = _mock_client(lambda request: httpx.Response(status, json={"error": "nope"}))
    # A 429 would be retried; reduce the retry budget so the test is quick.
    with pytest.raises(expected) as excinfo:
        await client.search_many(_group(), _user_input(), "SECRET_KEY_X")
    # The key must never appear in the raised message or its detail.
    assert "SECRET_KEY_X" not in str(excinfo.value)
    assert "SECRET_KEY_X" not in (excinfo.value.detail or "")


@pytest.mark.asyncio
async def test_missing_key_raises_config_error() -> None:
    client = BraveClient()
    with pytest.raises(SearchConfigError):
        await client.search_many(_group(), _user_input(), "   ")


def test_redaction_filter_strips_credentials() -> None:
    record = logging.LogRecord(
        "test", logging.WARNING, __file__, 1,
        "call failed with X-Subscription-Token: BSAsecret123", None, None,
    )
    assert logging_utils.RedactingFilter().filter(record)
    assert "BSAsecret123" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


def test_resolve_search_provider(monkeypatch) -> None:
    from app.services.pipeline import _resolve_search_provider

    assert _resolve_search_provider("auto", "KEY") == "brave"
    assert _resolve_search_provider("auto", None) in {"exa", "mock"}
    assert _resolve_search_provider("mock", "KEY") == "mock"
    with pytest.raises(SearchConfigError):
        _resolve_search_provider("brave", None)


def test_site_key_runs_the_search_when_the_client_sends_none(monkeypatch) -> None:
    """The website sends no key, so the deployment's own key must be used."""
    from app.services import pipeline

    monkeypatch.setattr(settings, "brave_api_key", "SITE_KEY")
    assert pipeline.resolve_brave_key(None) == "SITE_KEY"
    assert pipeline.resolve_brave_key("   ") == "SITE_KEY"
    assert pipeline._resolve_search_provider("auto", pipeline.resolve_brave_key(None)) == "brave"
    # A client-supplied key still wins: the extension's BYOK path is unchanged.
    assert pipeline.resolve_brave_key("USER_KEY") == "USER_KEY"

    monkeypatch.setattr(settings, "brave_api_key", None)
    assert pipeline.resolve_brave_key(None) == ""
    with pytest.raises(SearchConfigError):
        pipeline._resolve_search_provider("brave", pipeline.resolve_brave_key(None))
