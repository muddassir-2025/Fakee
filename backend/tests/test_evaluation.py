"""Accuracy gate over the labelled seed set.

This is a regression guard: it fails if a change drops precision or recall below
the project's bar. It is not a claim of general accuracy — the sample is small
and the labels are the project's own public-evidence review.
"""

from __future__ import annotations

from app.services.evaluation import (
    MIN_PRECISION,
    MIN_RECALL,
    evaluate,
    load_seed,
    predict_flagged,
)


def test_seed_metrics_meet_the_gate() -> None:
    metrics = evaluate(load_seed())
    assert metrics.tp + metrics.fn > 0, "no fake samples were evaluated"
    assert metrics.precision >= MIN_PRECISION, metrics.as_dict()
    assert metrics.recall >= MIN_RECALL, metrics.as_dict()


def test_no_real_post_is_flagged() -> None:
    """False positives on real postings are the costliest error — forbid them."""
    metrics = evaluate(load_seed())
    assert metrics.fp == 0, f"{metrics.fp} real posting(s) flagged as high risk"


def test_review_labelled_posts_are_excluded() -> None:
    metrics = evaluate(load_seed())
    assert metrics.skipped == 4  # JP-005..JP-008 are labelled "review"


def test_every_obvious_scam_is_flagged() -> None:
    fakes = [p for p in load_seed() if p["label"] == "fake"]
    missed = [p["id"] for p in fakes if not predict_flagged(p["text"])]
    assert missed == [], f"scams not flagged: {missed}"
