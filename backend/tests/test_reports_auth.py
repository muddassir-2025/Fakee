"""Sign-in gating, report ownership, and the admin review flow.

The suite runs without network access, so tokens are signed here with an Ed25519
key generated in-process and the JWKS client is stubbed to hand that key back.
That still exercises the real verification path (PyJWT EdDSA decode, issuer and
expiry checks) rather than skipping it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import auth as auth_module
from app.auth import AuthError, AuthUser, require_admin  # noqa: F401  (used via app deps)
from app.config import settings
from app.main import app

AUTH_BASE = "https://auth.example.neon.build/neondb/auth"
ISSUER = "https://auth.example.neon.build"
ADMIN_EMAIL = "studymuddassir@gmail.com"
SEEKER_EMAIL = "seeker@example.com"

_PRIVATE_KEY = Ed25519PrivateKey.generate()


class _StubJWKS:
    """Stands in for PyJWKClient: returns the public half of the test key."""

    def __init__(self) -> None:
        self.calls = 0

    def get_signing_key_from_jwt(self, _token: str):  # noqa: ANN001
        self.calls += 1
        return type("Key", (), {"key": _PRIVATE_KEY.public_key()})()


@pytest.fixture
def configured_auth(monkeypatch: pytest.MonkeyPatch):
    """Point the app at a Neon Auth project (and a stubbed key set)."""
    monkeypatch.setattr(settings, "neon_auth_base_url", AUTH_BASE)
    monkeypatch.setattr(settings, "neon_auth_jwks_url", None)
    monkeypatch.setattr(settings, "neon_auth_issuer", None)
    monkeypatch.setattr(settings, "admin_emails", ADMIN_EMAIL)
    monkeypatch.setattr(auth_module, "_jwks_client", lambda: _StubJWKS())
    yield


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
    app.dependency_overrides.clear()


def make_token(
    *,
    sub: str = "user-1",
    email: str | None = SEEKER_EMAIL,
    name: str | None = "Seeker",
    issuer: str = ISSUER,
    audience: str = ISSUER,
    expires_in: int = 600,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "iss": issuer,
        "aud": audience,
    }
    if email:
        payload["email"] = email
    if name:
        payload["name"] = name
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="EdDSA")


def act_as(user: AuthUser) -> None:
    app.dependency_overrides[auth_module.get_current_user] = lambda: user


# ------------------------------------------------------------------ token ----


def test_verify_token_accepts_a_signed_neon_token(configured_auth) -> None:
    user = auth_module.verify_token(make_token())
    assert user.id == "user-1"
    assert user.email == SEEKER_EMAIL
    assert user.display_name == "Seeker"
    assert user.is_admin is False


def test_verify_token_recognises_the_admin_address(configured_auth) -> None:
    user = auth_module.verify_token(make_token(sub="admin-1", email=ADMIN_EMAIL))
    assert user.is_admin is True
    # Case and surrounding whitespace must not defeat the allowlist.
    assert AuthUser(id="x", email=f"  {ADMIN_EMAIL.upper()} ").is_admin is True


def test_verify_token_rejects_an_expired_token(configured_auth) -> None:
    with pytest.raises(AuthError):
        auth_module.verify_token(make_token(expires_in=-60))


def test_verify_token_rejects_another_projects_issuer(configured_auth) -> None:
    """A token minted for a different auth project must not be replayable."""
    with pytest.raises(AuthError, match="issuer"):
        auth_module.verify_token(make_token(issuer="https://evil.example.test"))


def test_verify_token_rejects_a_tampered_signature(configured_auth) -> None:
    token = make_token()
    head, payload, signature = token.split(".")
    with pytest.raises(AuthError):
        auth_module.verify_token(f"{head}.{payload}.{signature[:-4]}AAAA")


def test_verify_token_rejects_a_malformed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage must be a 401, not a 500.

    Deliberately *not* the ``configured_auth`` fixture: that one stubs the JWKS
    client, so key resolution never runs. Through the real client PyJWT raises
    ``DecodeError`` while parsing the header (a ``PyJWTError`` but not a
    ``PyJWKClientError``), which used to escape unhandled and surface as an
    internal server error. Parsing fails before any key fetch, so no network
    access happens here.
    """
    monkeypatch.setattr(settings, "neon_auth_base_url", AUTH_BASE)
    monkeypatch.setattr(settings, "neon_auth_jwks_url", None)
    monkeypatch.setattr(settings, "neon_auth_issuer", None)
    auth_module.reset_jwks_cache()
    try:
        for bad in ("not-a-jwt", "a.b.c", ""):
            with pytest.raises(AuthError):
                auth_module.verify_token(bad)
    finally:
        auth_module.reset_jwks_cache()


def test_verify_token_requires_configuration() -> None:
    assert settings.auth_configured is False
    with pytest.raises(AuthError, match="not configured"):
        auth_module.verify_token(make_token())


def test_accepted_issuers_cover_origin_and_full_url(configured_auth) -> None:
    assert settings.accepted_issuers == [AUTH_BASE, ISSUER]


# ---------------------------------------------------------------- gating -----


@pytest.mark.asyncio
async def test_reporting_without_a_token_is_401(client, configured_auth, monkeypatch) -> None:
    # The dependency override must not be in play for the real gate to be tested.
    app.dependency_overrides.clear()
    response = await client.post("/api/reports", json={"text": "Company: Acme asked for a fee"})
    assert response.status_code == 401
    assert "sign in" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reporting_with_a_bad_token_is_401(client, configured_auth) -> None:
    response = await client.post(
        "/api/reports",
        json={"text": "Company: Acme asked for a fee"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_reporting_fails_closed_without_an_auth_project(client) -> None:
    """A misconfigured server must refuse the write, never accept it anonymously."""
    assert settings.auth_configured is False
    response = await client.post(
        "/api/reports",
        json={"text": "Company: Acme asked for a fee"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert response.status_code == 503
    assert "NEON_AUTH_BASE_URL" in response.json()["detail"]


@pytest.mark.asyncio
async def test_profile_and_admin_require_sign_in(client, configured_auth) -> None:
    app.dependency_overrides.clear()
    assert (await client.get("/api/reports/mine")).status_code == 401
    assert (await client.get("/api/admin/reports")).status_code == 401
    assert (await client.get("/api/admin/overview")).status_code == 401


@pytest.mark.asyncio
async def test_admin_area_rejects_a_non_admin(client, configured_auth) -> None:
    act_as(AuthUser(id="user-1", email=SEEKER_EMAIL, name="Seeker"))
    response = await client.get("/api/admin/reports")
    assert response.status_code == 403
    assert (await client.get("/api/admin/overview")).status_code == 403


# ----------------------------------------------------------- report flow -----


async def _file_report(client: httpx.AsyncClient, company: str) -> dict:
    response = await client.post(
        "/api/reports",
        json={
            "text": f"Company: {company}\nAsked for a Rs 1,500 registration fee over WhatsApp.",
            "report_type": "scam",
            "source": "extension_user",
            "risk_level": "HIGH",
            "risk_score": 78,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_report_records_who_filed_it_and_shows_up_in_the_profile(
    client, configured_auth
) -> None:
    # The test database is shared across runs, so every account id is unique per
    # test: a fixed id would accumulate reports and make the counts flaky.
    company = f"Acme {uuid4().hex[:8]}"
    act_as(AuthUser(id=f"seeker-{uuid4().hex[:8]}", email=SEEKER_EMAIL, name="Seeker"))
    report = await _file_report(client, company)

    assert report["status"] == "pending"
    assert report["company_name"] == company
    assert report["risk_level"] == "HIGH"
    assert report["risk_score"] == 78
    # The reporter's own view never carries another account's identity.
    assert "user_email" not in report

    profile = (await client.get("/api/reports/mine")).json()
    assert profile["user"]["email"] == SEEKER_EMAIL
    assert profile["user"]["is_admin"] is False
    assert [r["id"] for r in profile["reports"]] == [report["id"]]
    assert profile["counts"]["pending"] == 1


@pytest.mark.asyncio
async def test_one_account_cannot_see_another_accounts_reports(client, configured_auth) -> None:
    company = f"Privateco {uuid4().hex[:8]}"
    act_as(AuthUser(id=f"owner-{uuid4().hex[:8]}", email="owner@example.com"))
    report = await _file_report(client, company)

    act_as(AuthUser(id=f"stranger-{uuid4().hex[:8]}", email="stranger@example.com"))
    other = (await client.get("/api/reports/mine")).json()
    assert other["reports"] == []
    assert (await client.post(f"/api/reports/{report['id']}/withdraw")).status_code == 403


@pytest.mark.asyncio
async def test_the_reporter_can_withdraw_their_own_report(client, configured_auth) -> None:
    company = f"Withdrawco {uuid4().hex[:8]}"
    owner = AuthUser(id=f"owner-{uuid4().hex[:8]}", email="owner@example.com")
    act_as(owner)
    report = await _file_report(client, company)

    response = await client.post(f"/api/reports/{report['id']}/withdraw")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "withdrawn"

    profile = (await client.get("/api/reports/mine")).json()
    assert profile["reports"][0]["status"] == "withdrawn"
    assert profile["counts"]["withdrawn"] == 1
    assert profile["counts"]["pending"] == 0

    # Withdrawing is idempotent, not an error.
    assert (await client.post(f"/api/reports/{report['id']}/withdraw")).status_code == 200


@pytest.mark.asyncio
async def test_withdrawing_an_unknown_report_is_404(client, configured_auth) -> None:
    act_as(AuthUser(id="someone", email="someone@example.com"))
    assert (await client.post("/api/reports/does-not-exist/withdraw")).status_code == 404


# ------------------------------------------------------------ admin view -----


@pytest.mark.asyncio
async def test_admin_sees_the_reporter_and_can_reject_a_report(client, configured_auth) -> None:
    from app.db import SessionLocal
    from app.schemas import UserInput
    from app.services.repository import count_historical_reports

    company = f"Rejectco {uuid4().hex[:8]}"
    act_as(AuthUser(id=f"seeker-{uuid4().hex[:8]}", email="filer@example.com", name="Filer"))
    report = await _file_report(client, company)

    act_as(AuthUser(id=f"admin-{uuid4().hex[:8]}", email=ADMIN_EMAIL, name="Admin"))
    listing = (await client.get("/api/admin/reports", params={"q": company})).json()
    assert listing["total"] == 1
    entry = listing["reports"][0]
    assert entry["id"] == report["id"]
    assert entry["user_email"] == "filer@example.com"
    assert entry["user_name"] == "Filer"
    assert entry["description"].startswith(f"Company: {company}")

    user_input = UserInput(company={"name": company})
    async with SessionLocal() as session:
        assert await count_historical_reports(session, user_input) == 1

    rejected = await client.post(
        f"/api/admin/reports/{report['id']}/review",
        json={"action": "reject", "note": "Not a scam, wrong company."},
    )
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["reviewed_by"] == ADMIN_EMAIL
    assert body["reviewed_at"] is not None

    # A rejected report must stop inflating the company's history.
    async with SessionLocal() as session:
        assert await count_historical_reports(session, user_input) == 0

    # Returning it to the queue restores the signal.
    reset = await client.post(
        f"/api/admin/reports/{report['id']}/review", json={"action": "reset"}
    )
    assert reset.json()["status"] == "pending"
    async with SessionLocal() as session:
        assert await count_historical_reports(session, user_input) == 1

    approved = await client.post(
        f"/api/admin/reports/{report['id']}/review", json={"action": "approve", "note": "Confirmed."}
    )
    assert approved.json()["status"] == "approved"
    async with SessionLocal() as session:
        assert await count_historical_reports(session, user_input) == 1


@pytest.mark.asyncio
async def test_admin_list_rejects_an_unknown_status_filter(client, configured_auth) -> None:
    act_as(AuthUser(id="admin-1", email=ADMIN_EMAIL))
    response = await client.get("/api/admin/reports", params={"status": "nonsense"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_withdrawn_report_is_closed_to_review(client, configured_auth) -> None:
    company = f"Closedco {uuid4().hex[:8]}"
    owner = AuthUser(id=f"owner-{uuid4().hex[:8]}", email="owner@example.com")
    act_as(owner)
    report = await _file_report(client, company)
    await client.post(f"/api/reports/{report['id']}/withdraw")

    act_as(AuthUser(id="admin-1", email=ADMIN_EMAIL))
    response = await client.post(
        f"/api/admin/reports/{report['id']}/review", json={"action": "approve"}
    )
    assert response.status_code == 409
    assert (await client.post("/api/admin/reports/nope/review", json={"action": "approve"})).status_code == 404


@pytest.mark.asyncio
async def test_admin_overview_summarises_the_queue(client, configured_auth) -> None:
    company = f"Overviewco {uuid4().hex[:8]}"
    filer = f"seeker-{uuid4().hex[:8]}"
    act_as(AuthUser(id=filer, email="filer@example.com"))
    await _file_report(client, company)

    act_as(AuthUser(id=f"admin-{uuid4().hex[:8]}", email=ADMIN_EMAIL))
    body = (await client.get("/api/admin/overview")).json()
    assert body["auth_configured"] is True
    assert body["admins"] == [ADMIN_EMAIL]
    assert set(body["reports"]) == {"pending", "approved", "rejected", "withdrawn"}
    assert body["reports"]["pending"] >= 1
    assert body["reports_last_7_days"] >= 1
    assert body["users_reporting"] >= 1

    # The queue really holds this report (search is exercised, not just counts).
    queue = (await client.get("/api/admin/reports", params={"q": filer})).json()
    assert queue["total"] == 1
    assert queue["reports"][0]["company_name"] == company

    # Top companies is a ranked aggregate: well-formed and descending. It is not
    # asserted to contain one specific name — the shared test database has many
    # single-report companies competing for the same eight slots.
    top = body["top_companies"]
    assert top, "the dashboard must rank companies with reports"
    assert all(set(row) == {"company", "reports"} for row in top)
    counts = [row["reports"] for row in top]
    assert counts == sorted(counts, reverse=True)
    assert all(count >= 1 for count in counts)


@pytest.mark.asyncio
async def test_require_admin_gate_helper(client, configured_auth) -> None:
    """The dependency itself, not just the routes, refuses a non-admin."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await auth_module.require_admin(AuthUser(id="x", email="nobody@example.com"))
    assert excinfo.value.status_code == 403
    admin = await auth_module.require_admin(AuthUser(id="x", email=ADMIN_EMAIL))
    assert admin.email == ADMIN_EMAIL
