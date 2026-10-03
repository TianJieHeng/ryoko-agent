#!/usr/bin/env python3
"""Measure the bundled synthetic fixture only; no model, training, or network."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support a direct, offline invocation from any working directory.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.decisions.calibration import EvaluationError, evaluate  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--target-precision", type=float, default=.95)
    args = parser.parse_args(argv)
    fixture = json.loads(Path(__file__).with_name("synthetic_holdout.json").read_text(encoding="utf-8"))
    try:
        report = evaluate(fixture["rows"], provenance=fixture["provenance"],
                          target_precision=args.target_precision, bins=args.bins,
                          error_costs=fixture["error_costs"])
    except EvaluationError as exc:
        parser.error(str(exc))
    report["fixture_notice"] = fixture["notice"]
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
