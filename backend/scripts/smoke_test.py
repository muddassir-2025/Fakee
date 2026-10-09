"""End-to-end smoke test: runs the pipeline over sample inputs without HTTP.

Usage:
    python -m scripts.smoke_test            # from the backend/ directory
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.pipeline import run_investigation  # noqa: E402

SCAM_SAMPLE = """
Company: ABC Technologies
Internship: Software Development Intern
They contacted me on WhatsApp.
They said I was selected without an interview.
They offered Rs 40,000/month.
They said I have to pay Rs 1,500 registration fee.
They also said selected students will get a free laptop.
Website: abc-careers.xyz
"""

LEGIT_SAMPLE = """
Technology Internship Opportunity
Company: ADP
Location: Hyderabad
Role: GPT Intern (Technology)
Eligibility: B.Tech CSE/IT, 3rd/4th year, 70%+, no active backlogs
Selection Process: Online Assessment -> Technical Interviews -> HR Discussion
Apply here: https://jobs.adp.com/en/jobs/ind169248/gpt-interns-hyd-169248/
"""


async def main() -> None:
    # Start from a clean slate so results are reproducible run-to-run.
    from app.config import settings

    if settings.is_sqlite:
        db_file = settings.database_url.split("///", 1)[-1]
        Path(db_file).unlink(missing_ok=True)

    await init_db()
    for label, text in (("SCAM", SCAM_SAMPLE), ("LEGIT", LEGIT_SAMPLE)):
        async with SessionLocal() as session:
            result = await run_investigation(text, session=session, persist=True)
        print(f"\n===== {label} =====")
        print(f"company       : {result.input.company.name}")
        print(f"extraction    : {result.input.extraction_source}")
        print(f"risk          : {result.risk.level} ({result.risk.score}/100) conf={result.risk.confidence}")
        print(f"patterns      : {', '.join(s.pattern for s in result.investigation.detected_patterns)}")
        print(f"top signals   : {', '.join(s.id for s in result.risk.signals[:4])}")
        print(f"stages        : {[ (s.stage, s.status) for s in result.stages ]}")
        print(f"duration_ms   : {result.duration_ms}")


if __name__ == "__main__":
    asyncio.run(main())
