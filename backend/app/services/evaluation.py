"""Accuracy harness — measure the detector against labelled postings.

Runs the deterministic engine over the labelled seed set (and, if present, an
optional EMSCAD CSV) and reports precision / recall / F1 for the decision
"flag this posting as high risk". This exists so accuracy claims are *measured*
and regressions are caught, not asserted.

Labels come from the project's own public-evidence review; `review` entries are
excluded from the binary metric because they are genuinely undetermined.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..schemas import DomainIntel, ReviewSignals
from .extraction import heuristic_extract
from .patterns import detect_patterns
from .risk import assess

SEED_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "seed_posts.json"
# Optional: drop the EMSCAD "fake_job_postings.csv" here to evaluate on it too.
EMSCAD_PATH = Path(__file__).resolve().parents[2] / "data" / "emscad.csv"

# Fixed reference date so expiry is deterministic across runs.
FIXED_TODAY = date(2026, 10, 5)


@dataclass
class Metrics:
    tp: int = 0  # predicted high-risk, actually fake
    fp: int = 0  # predicted high-risk, actually real
    fn: int = 0  # predicted safe, actually fake
    tn: int = 0  # predicted safe, actually real
    skipped: int = 0  # unlabelled ("review") samples

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        denom = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / denom if denom else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "skipped": self.skipped,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "accuracy": round(self.accuracy, 3),
        }


def predict_flagged(text: str) -> bool:
    """True when the engine would warn the user (high risk or verified fraud)."""
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
    return risk.status == "FRAUDULENT_VERIFIED" or risk.level in {"HIGH", "CRITICAL"}


def load_seed(path: Path = SEED_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["posts"]


def load_emscad(path: Path = EMSCAD_PATH) -> list[dict]:
    """Optional EMSCAD rows, mapped to the same {text,label} shape."""
    if not path.exists():
        return []
    posts: list[dict] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            text = " ".join(
                filter(None, [row.get("title"), row.get("company_profile"),
                              row.get("description"), row.get("requirements")])
            )
            label = "fake" if str(row.get("fraudulent", "0")).strip() in {"1", "True", "true"} else "real"
            posts.append({"id": row.get("job_id", "?"), "text": text[:8000], "label": label})
    return posts


def evaluate(posts: list[dict]) -> Metrics:
    metrics = Metrics()
    for post in posts:
        label = post.get("label")
        if label not in {"fake", "real"}:
            metrics.skipped += 1
            continue
        expected_fake = label == "fake"
        predicted_fake = predict_flagged(post["text"])
        if expected_fake and predicted_fake:
            metrics.tp += 1
        elif expected_fake and not predicted_fake:
            metrics.fn += 1
        elif not expected_fake and predicted_fake:
            metrics.fp += 1
        else:
            metrics.tn += 1
    return metrics


# Minimum bar the harness enforces (regression gate).
MIN_PRECISION = 0.90
MIN_RECALL = 0.80


def run(include_emscad: bool = True) -> dict:
    seed = evaluate(load_seed())
    result = {"seed": seed.as_dict()}
    if include_emscad:
        emscad = load_emscad()
        if emscad:
            result["emscad"] = evaluate(emscad).as_dict()
    return result
