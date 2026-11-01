"""Provider-neutral errors raised by a user-keyed web search.

These let the API answer with a precise, actionable message — an invalid key, an
exhausted quota, a rate limit, or a provider that simply could not be reached —
and, critically, keep a *failed* search from being scored as a low-risk verdict.
A search that could not run is not the same as a search that ran and found little.
"""

from __future__ import annotations


class SearchProviderError(RuntimeError):
    """A search could not be completed. ``kind`` labels the failure for clients."""

    kind = "provider_error"
    # HTTP status the API should answer with for this failure.
    http_status = 502
    # Whether retrying the same request could plausibly succeed.
    retryable = False

    def __init__(self, message: str, *, provider: str = "brave", detail: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.detail = detail


class SearchAuthError(SearchProviderError):
    """The supplied API key was rejected (missing, expired, wrong plan)."""

    kind = "invalid_key"
    http_status = 401


class SearchQuotaError(SearchProviderError):
    """The key's allowance is exhausted (monthly quota, payment required)."""

    kind = "quota_exhausted"
    http_status = 429
    retryable = True


class SearchRateLimitError(SearchQuotaError):
    """A short-term rate limit was hit (retryable shortly)."""

    kind = "rate_limited"
    http_status = 429
    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class SearchNetworkError(SearchProviderError):
    """The search provider could not be reached (transport/timeout)."""

    kind = "network_error"
    http_status = 502
    retryable = True


class SearchConfigError(SearchProviderError):
    """A search was requested without the credential it needs."""

    kind = "missing_key"
    http_status = 400
