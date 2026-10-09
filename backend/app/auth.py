"""Authentication and authorization.

Sign-in is handled by **Neon Auth** (Managed Better Auth). The browser signs the
user in with Google and mints a short-lived JWT (15 minutes) via
``authClient.token()``; clients send it as ``Authorization: Bearer <token>`` and
everything here verifies it before a report is accepted or an admin action runs.

Verification is offline apart from fetching the project's public keys:

* the signature is checked against the JWKS at
  ``<NEON_AUTH_BASE_URL>/.well-known/jwks.json`` (Managed Better Auth signs with
  EdDSA/Ed25519, which ``PyJWT[crypto]`` supports), and
* the ``iss`` claim must be the origin (or full URL) of that auth project, so a
  token minted for a different project can never be replayed here.

Authorization is a plain email allowlist (``ADMIN_EMAILS``), because the product
has exactly one privileged role. Note that this trusts the *verified* token's
email claim, so keep Google as the only enabled provider — an unverified
email/password signup could otherwise claim the admin address.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError, PyJWTError

from .config import settings

logger = logging.getLogger("app.auth")

# Managed Better Auth uses EdDSA by default. The others are accepted so a project
# reconfigured to a different key type keeps working; the signing key still has
# to come from our own JWKS either way.
ALGORITHMS = ["EdDSA", "RS256", "ES256"]


class AuthError(Exception):
    """A bearer token that is missing, malformed, expired or not ours."""


@dataclass(frozen=True)
class AuthUser:
    """The subset of the Neon Auth user we need."""

    id: str
    email: str | None = None
    name: str | None = None
    email_verified: bool = False

    @property
    def display_name(self) -> str | None:
        return (self.name or "").strip() or None

    @property
    def is_admin(self) -> bool:
        email = (self.email or "").strip().lower()
        return bool(email) and email in settings.admin_email_list


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """One client (and therefore one key cache) per process."""
    return PyJWKClient(
        settings.jwks_url,
        cache_keys=True,
        lifespan=settings.neon_auth_jwks_cache_seconds,
    )


def reset_jwks_cache() -> None:
    """Drop the cached key set (used on a key rotation and by tests).

    Tolerant on purpose: the client is swappable (tests stub it), so refreshing
    must not depend on the replacement happening to be an ``lru_cache`` wrapper.
    """
    clear = getattr(_jwks_client, "cache_clear", None)
    if clear is not None:
        clear()


def verify_token(token: str) -> AuthUser:
    """Verify a raw JWT and return the user it belongs to. Never returns a
    partially-trusted user: any doubt raises :class:`AuthError`.

    Blocking (it may fetch the JWKS), so callers should use
    :func:`verify_bearer_token` from async code.
    """
    if not settings.auth_configured:
        raise AuthError("Authentication is not configured on this server.")
    return _decode_token(token, _signing_key_for(token))


def _signing_key_for(token: str):
    """Resolve the token's signing key, refreshing the key set once on failure.

    Only key *resolution* is retried. The cache holds one key set per process,
    so a rotation at the auth provider would otherwise reject valid tokens until
    that cache expired; but a token that resolved a key and then failed its
    claims (expired, tampered, wrong issuer) is genuinely bad and must not cost
    a second JWKS fetch.

    A string that is not a JWT at all must fail as a bad *token*, not a server
    error: PyJWT raises :class:`~jwt.exceptions.DecodeError` (a `PyJWTError` but
    not a `PyJWKClientError`) while parsing the header, before any key lookup, so
    it stays out of the retry path.
    """
    try:
        return _jwks_client().get_signing_key_from_jwt(token)
    except InvalidTokenError as exc:
        raise AuthError(f"Malformed token: {exc}") from exc
    except PyJWKClientError as exc:
        logger.info("Signing key lookup failed; refetching the key set: %s", exc)
        reset_jwks_cache()
        try:
            return _jwks_client().get_signing_key_from_jwt(token)
        except PyJWTError as retry_exc:
            raise AuthError(f"Signing key unavailable: {retry_exc}") from retry_exc


def _decode_token(token: str, signing_key) -> AuthUser:  # noqa: ANN001
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            # The issuer is validated below against the accepted set, and the
            # audience varies by project configuration, so both are off here.
            options={
                "verify_aud": False,
                "verify_iss": False,
                "require": ["exp", "sub"],
            },
        )
    except PyJWTError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc

    accepted = settings.accepted_issuers
    issuer = payload.get("iss")
    if accepted and issuer not in accepted:
        raise AuthError(f"Unexpected issuer: {issuer!r}")

    subject = str(payload.get("sub") or "").strip()
    if not subject:
        raise AuthError("Token has no subject.")

    return AuthUser(
        id=subject,
        email=(payload.get("email") or None),
        name=(payload.get("name") or None),
        email_verified=bool(payload.get("emailVerified") or payload.get("email_verified") or False),
    )


async def verify_bearer_token(token: str) -> AuthUser:
    """Async wrapper: key fetching and verification run in a worker thread."""
    return await asyncio.to_thread(verify_token, token)


def bearer_token(request: Request) -> str | None:
    """Read the token out of ``Authorization: Bearer …``."""
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def get_current_user(request: Request) -> AuthUser:
    """FastAPI dependency: a signed-in user, or 401/503.

    When no auth project is configured the endpoint fails *closed* — reporting is
    a write, so it must never be accepted anonymously just because the server is
    missing configuration.
    """
    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Sign-in is not configured on this server. Set NEON_AUTH_BASE_URL "
                "(backend) and VITE_NEON_AUTH_URL (frontend) to the same Neon Auth URL."
            ),
        )
    token = bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await verify_bearer_token(token)
    except AuthError as exc:
        logger.info("Rejected bearer token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your sign-in has expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_optional_user(request: Request) -> AuthUser | None:
    """Like :func:`get_current_user`, but anonymous callers are simply ``None``."""
    if not settings.auth_configured or not bearer_token(request):
        return None
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


async def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """FastAPI dependency: a signed-in user whose email is on the admin list."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This area is restricted to administrators.",
        )
    return user
