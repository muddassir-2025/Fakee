"""Stage 4 — domain verification.

Collects registration age (RDAP), DNS resolution, HTTPS support, redirect
behaviour, and whether the domain matches the claimed company name. Uses only
public, key-free endpoints (rdap.org) — no paid service required.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..schemas import DomainIntel
from .text_utils import (
    extract_domain,
    is_cheap_tld,
    is_shortener,
    normalize_name,
    tld_of,
)

logger = logging.getLogger(__name__)

# Well-known domains we should never flag as "recently registered" noise.
ESTABLISHED_DOMAINS = {
    "google.com", "adp.com", "amazon.com", "adobe.com", "microsoft.com",
    "infosys.com", "hcltech.com", "myntra.com", "linkedin.com", "ibm.com",
    "iiitg.ac.in", "eteaminc.com", "feuji.com", "csrbox.org", "aicte-india.org",
    "gov.in", "ac.in", "edu.in",
}


async def _resolve(domain: str) -> bool:
    loop = asyncio.get_running_loop()
    try:
        await loop.getaddrinfo(domain, None)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _rdap_lookup(domain: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(f"https://rdap.org/domain/{domain}")
            if response.status_code == 200:
                return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("RDAP lookup failed for %s: %s", domain, exc)
    return None


def _parse_rdap(rdap: dict) -> tuple[str | None, str | None, int | None]:
    """Return (registration_date, registrar, age_days)."""
    registration: str | None = None
    for event in rdap.get("events", []) or []:
        if event.get("eventAction") in {"registration", "registered"}:
            registration = event.get("eventDate")
            break

    registrar: str | None = None
    for entity in rdap.get("entities", []) or []:
        if "registrar" in (entity.get("roles") or []):
            vcard = entity.get("vcardArray") or []
            if len(vcard) > 1:
                for field in vcard[1]:
                    if field[0] == "fn" and len(field) > 3:
                        registrar = field[3]
                        break
            break

    age_days: int | None = None
    if registration:
        try:
            reg_dt = datetime.fromisoformat(registration.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - reg_dt).days
        except ValueError:
            pass
    return registration, registrar, age_days


async def _https_check(url: str) -> tuple[bool | None, bool | None, str | None]:
    """Return (https_enabled, reachable, final_url)."""
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(url)
            https = response.url.scheme == "https"
            return https, True, str(response.url)
    except Exception:  # noqa: BLE001
        return None, False, None


def _name_match(company_name: str | None, domain: str | None) -> bool | None:
    """Whether any label of the domain resembles the claimed company name.

    Considers every label (so ``jobs.adp.com`` matches "ADP") rather than only
    the first one, which would wrongly flag subdomains as mismatches.
    """
    if not company_name or not domain:
        return None
    labels = [normalize_name(part) for part in domain.split(".")[:-1]]
    labels = [label for label in labels if label]
    company = normalize_name(company_name)
    if not labels or not company:
        return None

    company_tokens = [t for t in _tokens(company_name) if len(t) > 2]
    for label in labels:
        if label in company or company.startswith(label) or company.endswith(label):
            return True
        for tok in company_tokens:
            if tok in label or label in tok:
                return True
    return False


def _tokens(name: str) -> list[str]:
    import re

    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()


async def check_domain(user_input) -> DomainIntel:
    domains = list(user_input.contacts.domains)
    website_domain = extract_domain(user_input.company.website)
    if website_domain and website_domain not in domains:
        domains.insert(0, website_domain)

    # A URL shortener (bit.ly, eej.at, lnkd.in, ...) is not the employer's own
    # domain. Checking it here would flag a name mismatch on every legit post
    # that hides its link — so it must never be used as the company domain.
    domains = [d for d in domains if d and not is_shortener(d)]

    if not domains:
        return DomainIntel(
            notes=["No employer domain provided (only a shortened link, if any)."]
        )

    domain = domains[0]
    intel = DomainIntel(domain=domain, tld=tld_of(domain), cheap_tld=is_cheap_tld(domain))

    dns_ok = await _resolve(domain)
    intel.dns_resolves = dns_ok

    https, reachable, final_url = await _https_check(f"https://{domain}")
    if https is None:
        https, reachable, final_url = await _https_check(f"http://{domain}")
    intel.https_enabled = https
    intel.reachable = reachable

    rdap = await _rdap_lookup(domain) if dns_ok else None
    if rdap:
        registration, registrar, age_days = _parse_rdap(rdap)
        intel.registration_date = registration
        intel.registrar = registrar
        intel.age_days = age_days

    intel.company_name_match = _name_match(user_input.company.name, domain)

    if intel.age_days is None:
        if domain in ESTABLISHED_DOMAINS:
            intel.notes.append("Domain belongs to a well-known organisation.")
        elif not dns_ok:
            intel.notes.append("Domain does not currently resolve via DNS.")
    elif intel.age_days < settings.domain_recent_days:
        intel.notes.append(
            f"Domain was registered only {intel.age_days} days ago."
        )
    else:
        intel.notes.append(f"Domain has existed for roughly {intel.age_days} days.")

    if final_url and extract_domain(final_url) != domain:
        intel.notes.append(f"Website redirects to {final_url}.")
    if intel.cheap_tld:
        intel.notes.append(f"Uses a low-cost TLD (.{intel.tld}).")
    if intel.company_name_match is False:
        intel.notes.append("Domain does not obviously match the claimed company name.")

    return intel
