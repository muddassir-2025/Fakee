"""Run the accuracy harness.

    python -m scripts.evaluate

Prints precision/recall/F1 over the labelled seed set (and EMSCAD if present)
and exits non-zero when the result falls below the regression gate.
"""

from __future__ import annotations

import json
import sys

from app.services.evaluation import MIN_PRECISION, MIN_RECALL, run


def main() -> int:
    report = run()
    print(json.dumps(report, indent=2))

    ok = True
    for name, m in report.items():
        if m["precision"] < MIN_PRECISION or m["recall"] < MIN_RECALL:
            ok = False
            print(
                f"[FAIL] {name}: precision {m['precision']} (min {MIN_PRECISION}), "
                f"recall {m['recall']} (min {MIN_RECALL})",
                file=sys.stderr,
            )
        else:
            print(
                f"[OK]   {name}: precision {m['precision']}, recall {m['recall']}, f1 {m['f1']}"
            )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
