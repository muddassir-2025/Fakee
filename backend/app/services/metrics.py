"""In-process metrics registry.

Deliberately dependency-free: counters and latency histograms kept in process
memory and exposed at ``/api/metrics`` (JSON) and ``/api/metrics/prometheus``
(text). This is enough to power a dashboard, a scrape, or an alert on a single
instance without pulling in a client library. Values reset on restart, which is
the expected behaviour for process-local operational metrics.

Only low-cardinality labels should be used (method, route template, status,
outcome, stage). Never label by raw user input.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

_STARTED_AT = time.time()
_LOCK = threading.Lock()

# (name, sorted-label-tuple) -> value
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
# observation-key -> {"count", "sum", "buckets": {upper_bound: cumulative}}
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], dict] = {}

# Latency buckets in seconds (upper bounds), cumulative like Prometheus.
LATENCY_BUCKETS: tuple[float, ...] = (
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)


def _key(name: str, labels: dict[str, object] | None) -> tuple[str, tuple]:
    items = tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))
    return (name, items)


def inc(name: str, labels: dict[str, object] | None = None, value: float = 1.0) -> None:
    key = _key(name, labels)
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + value


def observe(name: str, seconds: float, labels: dict[str, object] | None = None) -> None:
    """Record one observation of a duration (in seconds) into a histogram."""
    key = _key(name, labels)
    with _LOCK:
        hist = _HISTOGRAMS.get(key)
        if hist is None:
            hist = {
                "count": 0,
                "sum": 0.0,
                "buckets": {bound: 0 for bound in LATENCY_BUCKETS},
            }
            _HISTOGRAMS[key] = hist
        hist["count"] += 1
        hist["sum"] += seconds
        for bound in LATENCY_BUCKETS:
            if seconds <= bound:
                hist["buckets"][bound] += 1


def uptime_seconds() -> float:
    return max(0.0, time.time() - _STARTED_AT)


def snapshot() -> dict:
    """JSON-friendly view of all metrics."""
    counters: dict[str, dict[str, float]] = defaultdict(dict)
    with _LOCK:
        for (name, labels), value in _COUNTERS.items():
            counters[name]["|".join(f"{k}={v}" for k, v in labels) or "total"] = value
        histograms: dict[str, list[dict]] = defaultdict(list)
        for (name, labels), hist in _HISTOGRAMS.items():
            histograms[name].append(
                {
                    "labels": "|".join(f"{k}={v}" for k, v in labels) or "total",
                    "count": hist["count"],
                    "sum": round(hist["sum"], 6),
                    "avg": round(hist["sum"] / hist["count"], 6) if hist["count"] else 0.0,
                    "buckets": {
                        str(bound): hist["buckets"][bound] for bound in LATENCY_BUCKETS
                    },
                }
            )
    return {
        "uptime_seconds": round(uptime_seconds(), 3),
        "counters": dict(counters),
        "histograms": dict(histograms),
    }


def render_prometheus() -> str:
    """Prometheus text exposition format."""
    lines: list[str] = []
    with _LOCK:
        for (name, labels), value in sorted(_COUNTERS.items()):
            metric = _prom_name(name) + "_total" if not name.endswith("_total") else _prom_name(name)
            lines.append(f"{metric}{_prom_labels(labels)} {value}")
        for (name, labels), hist in sorted(_HISTOGRAMS.items()):
            base = _prom_name(name)
            for bound in LATENCY_BUCKETS:
                bucket_labels = labels + (("le", _fmt_bound(bound)),)
                lines.append(
                    f"{base}_bucket{_prom_labels(bucket_labels)} "
                    f"{hist['buckets'][bound]}"
                )
            inf_labels = labels + (("le", "+Inf"),)
            lines.append(f"{base}_bucket{_prom_labels(inf_labels)} {hist['count']}")
            lines.append(f"{base}_sum{_prom_labels(labels)} {hist['sum']}")
            lines.append(f"{base}_count{_prom_labels(labels)} {hist['count']}")
    lines.append(f"process_uptime_seconds {round(uptime_seconds(), 3)}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    """Clear all metrics (used by tests)."""
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def _prom_name(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _prom_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_bound(bound: float) -> str:
    return f"{bound:g}"
