"""Boot the real uvicorn server in a thread and exercise the HTTP API.

Usage:  python -m scripts.http_test
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

SAMPLE = """Company: ABC Technologies
Internship: Software Development Intern
They contacted me on WhatsApp and said I was selected without an interview.
They offered Rs 40,000/month but asked for a Rs 1,500 registration fee.
Website: abc-careers.xyz"""


def main() -> int:
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for startup. Probe the dependency-free liveness endpoint: /api/health
    # performs a real database round-trip, which can take seconds on a cold
    # connection (especially against a remote Postgres), so a short-timeout
    # probe against it times out even though the server is already serving.
    for _ in range(120):
        try:
            if httpx.get(f"{BASE}/api/health/live", timeout=5.0).status_code == 200:
                break
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(0.25)
    else:
        print("server did not start")
        return 1

    ok = True
    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        health = client.get("/api/health").json()
        print("health        :", health["status"], health["integrations"], "mock_mode=", health["mock_mode"])

        resp = client.post("/api/investigate", json={"text": SAMPLE, "persist": True})
        resp.raise_for_status()
        data = resp.json()
        print("investigate   :", data["risk"]["level"], data["risk"]["score"], "id=", data["id"])
        print("signals       :", [s["id"] for s in data["risk"]["signals"][:5]])

        listing = client.get("/api/investigations?limit=5").json()
        print("list          :", len(listing), "item(s)")
        assert any(item["id"] == data["id"] for item in listing), "new investigation not listed"
        ok &= any(item["id"] == data["id"] for item in listing)

        fetched = client.get(f"/api/investigations/{data['id']}")
        fetched.raise_for_status()
        print("reload        :", fetched.status_code, fetched.json()["risk"]["level"])

        patterns = client.get("/api/patterns").json()
        print("pattern count :", len(patterns))

        stats = client.get("/api/stats").json()
        print("stats         :", stats)

        # CORS preflight
        pre = client.options(
            "/api/investigate",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        print("cors          :", pre.status_code, pre.headers.get("access-control-allow-origin"))
        ok &= pre.headers.get("access-control-allow-origin") == "http://localhost:5173"

        missing = client.get("/api/investigations/does-not-exist")
        print("404 handling  :", missing.status_code)
        ok &= missing.status_code == 404

    server.should_exit = True
    thread.join(timeout=10)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
