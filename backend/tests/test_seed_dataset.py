"""Regression guards over the research pack's seed dataset.

Counts and labels come from the project's own public-evidence review. They are
used here only to catch regressions — chiefly (a) never calling a real post a
verified fraud, and (b) still catching documented scams — not as a claim of
measured accuracy.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.schemas import DomainIntel, ReviewSignals
from app.services.deadline import detect_deadline
from app.services.extraction import heuristic_extract
from app.services.patterns import detect_patterns
from app.services.risk import assess

FIXED_TODAY = date(2026, 10, 5)  # matches the pack's date_checked

POSTS = json.loads(
    (Path(__file__).parent / "fixtures" / "seed_posts.json").read_text(encoding="utf-8")
)["posts"]
BY_ID = {p["id"]: p for p in POSTS}
REAL = [p for p in POSTS if p["source"] != "synthetic"]
SYNTHETIC = [p for p in POSTS if p["source"] == "synthetic"]


def _run(post):
    text = post["text"]
    user_input = heuristic_extract(text)
    patterns = detect_patterns(
        user_input, DomainIntel(), ReviewSignals(total_mentions=0), sources_checked=0
    )
    risk = assess(
        patterns,
        user_input=user_input,
        evidence=[],
        sources_checked=0,
        used_mock=True,
        today=FIXED_TODAY,
    )
    return user_input, patterns, risk


def test_seed_dataset_shape() -> None:
    assert len(POSTS) == 18
    assert len(REAL) == 12
    assert len(SYNTHETIC) == 6


def test_real_posts_are_never_flagged_as_verified_fraud() -> None:
    offenders = [p["id"] for p in REAL if _run(p)[2].status == "FRAUDULENT_VERIFIED"]
    assert offenders == [], f"real posts flagged as verified fraud: {offenders}"


def test_negated_fees_are_not_read_as_payment_requests() -> None:
    # JP-005 repeats "No Registration Fee / No Deposit / No Bond"; JP-009 is a
    # "free of cost" initiative. Neither should register a payment request.
    for pid in ("JP-005", "JP-009"):
        user_input, patterns, _ = _run(BY_ID[pid])
        assert user_input.money_request.detected is False, pid
        assert "upfront_payment" not in {p.pattern for p in patterns}, pid


def test_password_signup_is_not_a_sensitive_data_request() -> None:
    # JP-008 tells students to "Create a strong password" on a signup page.
    _user_input, patterns, risk = _run(BY_ID["JP-008"])
    assert "sensitive_data_request" not in {p.pattern for p in patterns}
    assert risk.status != "FRAUDULENT_VERIFIED"


def test_obvious_synthetic_scams_are_high_risk() -> None:
    for post in SYNTHETIC:
        risk = _run(post)[2]
        assert risk.level in {"HIGH", "CRITICAL"}, (post["id"], risk.level)


def test_payment_scams_are_verified_fraudulent() -> None:
    for pid in ("SYN-001", "SYN-003", "SYN-004", "SYN-005"):
        assert _run(BY_ID[pid])[2].status == "FRAUDULENT_VERIFIED", pid


def test_expired_genuine_post_is_not_fraud() -> None:
    # The Adobe hackathon is genuine but its 8 Aug 2026 deadline has passed.
    risk = _run(BY_ID["JP-002"])[2]
    assert risk.expired is True
    assert risk.status == "EXPIRED"


def test_deadline_detection_parses_common_formats() -> None:
    cases = {
        "Last Date to Register: 8 August 2026 (11:59 PM IST)": date(2026, 8, 8),
        "Registration Last Date: 19th July 2026": date(2026, 7, 19),
        "Last date to apply: July 14, 2026.": date(2026, 7, 14),
        "Registration Deadline: 05 August 2026, 2:00 PM (IST)": date(2026, 8, 5),
    }
    for text, expected in cases.items():
        info = detect_deadline(text, today=FIXED_TODAY)
        assert info.deadline == expected, text
        assert info.expired is True

    future = detect_deadline("Last Date to Register: 8 August 2027", today=FIXED_TODAY)
    assert future.deadline == date(2027, 8, 8)
    assert future.expired is False
