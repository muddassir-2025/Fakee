"""SSRF-hardened server-side page fetch.

The backend reads public pages on behalf of a user when it enriches a Brave
search with page text. A URL that comes from a search provider is *untrusted*:
it can point at the loopback interface, a private network, a link-local
metadata endpoint (169.254.169.254), or use a redirect to smuggle one of those
past a first check. This module is the single choke point for those fetches and
enforces the guards in one place:

* only ``http``/``https`` URLs,
* the host resolves to at least one address and **every** resolved address is a
  public, routable one (so a DNS answer that mixes public and private is
  rejected),
* redirects are followed manually and **each hop is revalidated** before it is
  requested, so an open redirect cannot land on an internal host,
* a per-request timeout, a capped response size, and an accepted content-type
  list bound the work a hostile page can cause.

The fetched text is treated as untrusted content everywhere downstream — it is
only ever scanned for signal sentences and handed to the model as evidence, never
executed or reflected.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)

# Content types we are willing to read as page text. Anything else (a binary,
# an image, a JSON API) is skipped rather than decoded as HTML.
TEXT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.I | re.S)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


class UnsafeUrlError(ValueError):
    """The URL is not one the server is willing to fetch (SSRF guard)."""


@dataclass
class FetchResult:
    url: str
    title: str
    text: str
    content_type: str


def _is_public_ip(value: str) -> bool:
    """True only for a globally routable address.

    ``is_global`` already excludes loopback, private, link-local (including the
    cloud metadata address 169.254.169.254), reserved, multicast and the
    unspecified address, and it is version-agnostic, so both an A and an AAAA
    answer are covered by one check.
    """
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_global


def _resolve_addresses(host: str) -> list[str]:
    """Every address the host currently resolves to (raises if none)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise UnsafeUrlError(f"Could not resolve host {host!r}.") from exc
    addresses: list[str] = []
    for *_, sockaddr in infos:
        if sockaddr and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    if not addresses:
        raise UnsafeUrlError(f"Host {host!r} did not resolve to an address.")
    return addresses


def validate_url(url: str) -> str:
    """Validate a URL for a server-side fetch, or raise ``UnsafeUrlError``.

    Enforces scheme, an explicit host, and a resolution where **every** answer is
    a public address. Called again for every redirect hop, which is what stops a
    public URL from redirecting to an internal one.
    """
    candidate = (url or "").strip()
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http and https URLs may be fetched.")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("The URL has no host.")
    # A literal IP can be checked directly; a name is resolved first.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = _resolve_addresses(host)
        if not all(_is_public_ip(addr) for addr in addresses):
            raise UnsafeUrlError(
                "The host resolves to a private or non-routable address."
            )
    else:
        if not _is_public_ip(str(literal)):
            raise UnsafeUrlError("The host is a private or non-routable address.")
    return candidate


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, readable text) from raw HTML with no external deps."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = _TAG_RE.sub(" ", title_match.group(1)).strip() if title_match else ""

    body = _SCRIPT_RE.sub(" ", html)
    # Drop the head so nav/analytics markup below it does not dominate.
    head_end = body.lower().find("</head>")
    if head_end != -1:
        body = body[head_end + len("</head>") :]
    text = _TAG_RE.sub(" ", body)
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANKS_RE.sub("\n\n", text).strip()
    return title[:512], text


async def safe_fetch(
    url: str,
    *,
    timeout_seconds: float = 12.0,
    max_bytes: int = 400_000,
    max_redirects: int = 3,
    user_agent: str = "FakeeBot/1.0 (+https://github.com/muddassir-2025/Fakee)",
) -> FetchResult | None:
    """Fetch and text-extract a public page, or return ``None``.

    Never raises for a page that merely could not be read: a failed enrichment
    leaves the search snippets in place. It *does* refuse unsafe destinations by
    returning ``None`` (and logging why), because that is a hostile input rather
    than an ordinary failure.
    """
    try:
        current = validate_url(url)
    except UnsafeUrlError as exc:
        logger.info("Refusing unsafe fetch destination %r: %s", url, exc)
        return None

    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,text/plain"}
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers=headers,
        ) as client:
            for _ in range(max_redirects + 1):
                response = await client.get(current)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        break
                    nxt = urljoin(current, location)
                    # Revalidate every hop before we request it.
                    current = validate_url(nxt)
                    continue

                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type and not any(
                    content_type.startswith(allowed) for allowed in TEXT_CONTENT_TYPES
                ):
                    logger.info("Skipping %s: content-type %s", current, content_type)
                    return None

                # ``response.text`` is decoded from the whole body, so cap the
                # raw bytes the provider can make us read.
                raw = response.content[:max_bytes]
                encoding = response.encoding or "utf-8"
                html = raw.decode(encoding, errors="replace")
                title, text = extract_text(html)
                if not text:
                    return None
                return FetchResult(
                    url=str(response.url), title=title, text=text, content_type=content_type
                )
    except UnsafeUrlError as exc:
        logger.info("Refusing unsafe redirect for %r: %s", url, exc)
        return None
    except httpx.HTTPError as exc:
        logger.info("Page fetch failed for %s: %s", current, exc)
        return None
    return None
