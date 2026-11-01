"""SSRF guard tests for the server-side page fetcher."""

from __future__ import annotations

import pytest

from app.services.safe_fetch import UnsafeUrlError, _is_public_ip, extract_text, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "https://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "not-a-url",
        "http:///nohost",
    ],
)
def test_validate_url_rejects_unsafe_destinations(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_are_allowed(ip: str) -> None:
    assert _is_public_ip(ip) is True


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "::1", "0.0.0.0"])
def test_private_ips_are_rejected(ip: str) -> None:
    assert _is_public_ip(ip) is False


def test_validate_url_accepts_a_public_literal() -> None:
    assert validate_url("https://8.8.8.8/page") == "https://8.8.8.8/page"


def test_extract_text_strips_markup_and_scripts() -> None:
    html = (
        "<html><head><title>Hello</title><style>.x{}</style></head>"
        "<body><h1>Title</h1><script>alert(1)</script><p>Body text here.</p></body></html>"
    )
    title, text = extract_text(html)
    assert title == "Hello"
    assert "alert" not in text
    assert "Body text here." in text
