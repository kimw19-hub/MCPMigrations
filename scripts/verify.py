"""Compare a fresh run against the expected outputs in results/expected/.

Every statistic is compared, not a sample of them. The only value allowed to
differ is the snapshot date, which records when the run happened.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IGNORED = {"snapshot_date"}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            if key not in IGNORED:
                out.update(_flatten(inner, f"{prefix}{key}."))
        return out
    if isinstance(value, list):
        out = {}
        for i, inner in enumerate(value):
            out.update(_flatten(inner, f"{prefix}{i}."))
        return out
    return {prefix.rstrip("."): value}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return bool(a == b)


def compare_summary() -> list[str]:
    expected = _flatten(json.loads((ROOT / "results/expected/summary.json").read_text()))
    actual = _flatten(json.loads((ROOT / "results/summary.json").read_text()))
    problems = [f"missing: {k}" for k in sorted(expected.keys() - actual.keys())]
    problems += [f"unexpected: {k}" for k in sorted(actual.keys() - expected.keys())]
    problems += [
        f"{k}: expected {expected[k]!r}, got {actual[k]!r}"
        for k in sorted(expected.keys() & actual.keys())
        if not _same(expected[k], actual[k])
    ]
    return problems


def compare_numbers() -> list[str]:
    """The macro values, line by line."""

    def lines(path: Path) -> set[str]:
        return {
            line
            for line in path.read_text().splitlines()
            if line.startswith("\\def") and "snapshotDate" not in line
        }

    expected = lines(ROOT / "results/expected/numbers.tex")
    actual = lines(ROOT / "results/numbers.tex")
    return [f"numbers.tex differs: {line}" for line in sorted(expected ^ actual)]


def main() -> int:
    problems = compare_summary() + compare_numbers()
    if problems:
        print(f"{len(problems)} difference(s) from the expected results:")
        for problem in problems:
            print("  " + problem)
        return 1
    print("reproduced: every statistic and every number matches results/expected/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
