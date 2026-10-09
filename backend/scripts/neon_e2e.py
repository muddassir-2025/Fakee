"""End-to-end check against the real database (Neon) over HTTP.

Boots the actual uvicorn app in a daemon thread using the ambient configuration
(so the project-root .env -> Neon DATABASE_URL is used), runs an investigation,
then proves it persisted and can be reloaded from a *fresh* database connection.

Usage:  python -m scripts.neon_e2e
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

PORT = 8124
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    if settings.is_sqlite:
        print("!! DATABASE_URL resolves to SQLite, not Neon. Aborting.")
        return 2
    print("database      :", "sqlite" if settings.is_sqlite else "postgresql")

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(60):
        try:
            httpx.get(f"{BASE}/api/health", timeout=1.0)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    else:
        print("server did not start")
        return 1

    ok = True
    marker = f"NeonE2E-{uuid.uuid4().hex[:8]}"
    sample = (
        f"Company: {marker} Analytics Pvt Ltd\n"
        "Internship: Data Intern\n"
        "Selection: Online assessment, technical interview, HR round\n"
        "Apply at https://example.com/careers"
    )

    with httpx.Client(base_url=BASE, timeout=180.0) as client:
        health = client.get("/api/health").json()
        print("health        :", health["status"], health["integrations"], "mock_mode=", health["mock_mode"])
        ok &= health["integrations"]["database"] == "postgresql"

        resp = client.post("/api/investigate", json={"text": sample, "persist": True})
        resp.raise_for_status()
        data = resp.json()
        inv_id = data["id"]
        print("investigate   :", data["risk"]["level"], data["risk"]["score"], "id=", inv_id)
        ok &= marker in (data["input"]["company"]["name"] or "")

        listing = client.get("/api/investigations?limit=50").json()
        listed = any(item["id"] == inv_id for item in listing)
        print("list          :", len(listing), "item(s); contains new:", listed)
        ok &= listed

        fetched = client.get(f"/api/investigations/{inv_id}")
        fetched.raise_for_status()
        reloaded = fetched.json()
        print("reload via API:", fetched.status_code, reloaded["risk"]["level"], reloaded["input"]["company"]["name"])
        ok &= marker in (reloaded["input"]["company"]["name"] or "")

    server.should_exit = True
    thread.join(timeout=10)

    # Independent proof: read the row back on a brand-new engine/connection.
    import asyncio  # noqa: E402

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

    from app.services.repository import get_investigation  # noqa: E402

    async def verify() -> bool:
        fresh_engine = create_async_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)
        try:
            async with AsyncSession(fresh_engine) as session:
                record = await get_investigation(session, inv_id)
                if record is None:
                    return False
                stored = record.user_input or {}
                name = (stored.get("company") or {}).get("name") if isinstance(stored, dict) else stored.company.name
                return marker in (name or "")
        finally:
            await fresh_engine.dispose()

    fresh = asyncio.run(verify())
    print("fresh readback:", fresh)
    ok &= fresh

    print("\nRESULT:", "PASS" if ok else "FAIL", f"(persisted id={inv_id})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
