"""Lightweight text/intelligence helpers shared across services."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Indian (+91) and generic international phone-ish patterns.
PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{5,5}[\s-]?\d{5,5}"
)
PHONE_RE_IN = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")

# TLDs commonly abused for throwaway recruitment/landing sites.
CHEAP_TLDS = {
    "xyz", "top", "club", "online", "site", "website", "space", "icu", "live",
    "buzz", "click", "link", "info", "tk", "ml", "ga", "cf", "gq", "work",
    "cam", "rest", "shop", "store", "monster", "cyou", "sbs", "bar", "quest",
}

# URL shorteners that hide the real destination.
SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly",
    "rb.gy", "cutt.ly", "shorturl.at", "rebrand.ly", "lnkd.in", "eej.at",
    "youtu.be", "t.ly", "short.gy", "rb.link",
}

# Free/major public mail providers (a "company" on gmail is a weak signal).
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com",
    "protonmail.com", "proton.me", "icloud.com", "aol.com", "yandex.com",
    "mail.com", "gmx.com", "zoho.com", "live.com",
}


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for match in URL_RE.findall(text or ""):
        cleaned = match.rstrip(".,;:)")
        if cleaned not in seen:
            seen.append(cleaned)
    # Also accept bare domains like "abc-careers.xyz" without a scheme.
    for domain in extract_bare_domains(text):
        if not any(domain in url for url in seen):
            seen.append(domain)
    return seen


# TLDs we recognise when a domain is written without a scheme.
KNOWN_TLDS = CHEAP_TLDS | {
    "com", "org", "net", "edu", "gov", "mil", "int", "io", "co", "in",
    "us", "uk", "ai", "app", "dev", "tech", "me", "biz", "ac", "cloud",
    "page", "one", "pro", "cc", "tv", "gg", "sh", "to", "fm",
}
BARE_DOMAIN_RE = re.compile(
    r"(?<![\w@./-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24}))(?![\w-])"
)


def extract_bare_domains(text: str) -> list[str]:
    """Find domain-looking strings without a scheme (case-sensitive, lowercase TLDs)."""
    seen: list[str] = []
    for match in BARE_DOMAIN_RE.findall(text or ""):
        domain = match[0].lower()
        if match[1] not in KNOWN_TLDS:
            continue
        if domain not in seen:
            seen.append(domain)
    return seen


def extract_emails(text: str) -> list[str]:
    seen: list[str] = []
    for match in EMAIL_RE.findall(text or ""):
        low = match.lower()
        if low not in seen:
            seen.append(low)
    return seen


def extract_phones(text: str) -> list[str]:
    seen: list[str] = []
    for match in PHONE_RE_IN.findall(text or ""):
        if match not in seen:
            seen.append(match)
    return seen


def extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return None
    host = host.lower().strip().lstrip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host


def domains_from_urls(urls: list[str]) -> list[str]:
    seen: list[str] = []
    for url in urls:
        domain = extract_domain(url)
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def tld_of(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = domain.split(".")
    return parts[-1] if len(parts) > 1 else None


def is_shortener(domain: str | None) -> bool:
    if not domain:
        return False
    return domain in SHORTENER_DOMAINS or any(
        domain.endswith("." + s) for s in SHORTENER_DOMAINS
    )


def is_cheap_tld(domain: str | None) -> bool:
    return (tld_of(domain) or "") in CHEAP_TLDS


def is_free_email(domain: str | None) -> bool:
    return (domain or "") in FREE_EMAIL_DOMAINS


# A negation immediately before a money/risk term flips its meaning:
# "no registration fee", "without any deposit", "never asked for money".
# Allows a few words in between ("no, under any circumstance, a fee").
NEGATION_PREFIX_RE = re.compile(
    r"(?:\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bzero\b|\bnothing\b|\bfree\s+of\b)"
    r"(?:\W+\w+){0,3}\W+$",
    re.IGNORECASE,
)


def is_negated(text: str, index: int, window: int = 48) -> bool:
    """True when the term starting at ``index`` is negated just before it."""
    prefix = (text or "")[max(0, index - window) : index]
    return bool(NEGATION_PREFIX_RE.search(prefix))


def has_unnegated(text: str, terms: list[str] | set[str] | tuple[str, ...]) -> bool:
    """True when any term appears at least once *without* a preceding negation.

    This is what stops a post that says "No Registration Fee / No Deposit"
    from being flagged as demanding an upfront payment.
    """
    low = (text or "").lower()
    for term in terms:
        term = term.lower()
        if not term:
            continue
        start = 0
        while True:
            idx = low.find(term, start)
            if idx == -1:
                break
            if not is_negated(low, idx):
                return True
            start = idx + len(term)
    return False


def normalize_name(name: str | None) -> str:
    """Normalize a company name for matching (lowercase alphanumerics only)."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def strip_code_fences(content: str) -> str:
    """Remove ```json ... ``` fences an LLM may add around JSON."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def safe_json_loads(content: str) -> dict | list | None:
    """Best-effort JSON parse, tolerant of fences and leading prose."""
    if not content:
        return None
    text = strip_code_fences(content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} or [...] block.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
