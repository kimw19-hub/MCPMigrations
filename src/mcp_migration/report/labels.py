"""Agreement and precision from the manually labeled sample.

The labeling sheet is coded independently by two people; this module reads the
coded sheets back and computes the quality figures the paper reports, so they
are derived from the data rather than tallied by hand.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

YES = "yes"


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def normalize(value: str) -> str:
    """Canonicalise a cell before comparing two coders.

    Multi-valued cells are semicolon-separated sets, not sequences, so
    ``roots:kept;sampling:removed`` and ``sampling:removed;roots:kept`` are the
    same answer. Compared literally they would count as a disagreement and drag
    the column's kappa toward zero while measuring only typing order.
    """
    parts = [p.strip().lower() for p in value.split(";")]
    return ";".join(sorted(p for p in parts if p))


def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Cohen's kappa for two equal-length sequences of categorical labels.

    Returns 1.0 when both coders agree on every item and only one category is
    used: chance agreement is then also 1.0, which would otherwise be 0/0.
    """
    if len(a) != len(b):
        raise ValueError("coder sequences must be the same length")
    n = len(a)
    if n == 0:
        return 0.0
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    count_a, count_b = Counter(a), Counter(b)
    expected = sum(count_a[k] * count_b[k] for k in set(count_a) | set(count_b)) / (n * n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def precision(rows: Iterable[Mapping[str, str]], column: str = "detector_correct") -> float:
    """Share of labeled episodes confirmed as genuine migration activity."""
    judged = [str(r.get(column, "")).strip().lower() for r in rows]
    judged = [v for v in judged if v]
    return sum(v == YES for v in judged) / len(judged) if judged else 0.0


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Wilson rather than the normal approximation because per-kind precision is
    measured on tens of episodes, not hundreds, and is expected near 1.0 --
    exactly where the normal interval misbehaves, running past 1 or collapsing
    to zero width when every judgement agrees.
    """
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _stratum(episode: Mapping[str, Any] | None, row: Mapping[str, str]) -> tuple[str, str]:
    """The (kind, head era) stratum the sample was drawn over; see sampling.py."""
    source: Mapping[str, Any] = episode if episode else row
    return str(source.get("kind") or "unknown"), str(source.get("head_static_era") or "unknown")


def weighted_precision(
    rows: Iterable[Mapping[str, str]],
    episodes_by_id: Mapping[str, Mapping[str, Any]],
    kind: str | None = None,
    column: str = "detector_correct",
) -> dict[str, float | int]:
    """Precision of the dataset, estimated from a stratified sample.

    The sample takes the same number of episodes from every (kind, era)
    stratum, however many episodes each holds, so the plain share of "yes"
    over sampled rows weights a stratum of forty episodes as heavily as one of
    eleven thousand. Each stratum's precision is weighted by its share of the
    episodes instead, which is what the paper's figure has to mean.

    The interval is Wilson's on an effective sample size (Korn and Graubard,
    1998): the n at which a simple random sample would give the stratified
    estimate's variance, with a finite-population correction per stratum. Where
    every judgement agrees the variance is zero, and the plain n is used.
    ``coverage`` is the share of the population in strata that were judged at all.
    """
    population = Counter(
        _stratum(e, {}) for e in episodes_by_id.values() if kind is None or e.get("kind") == kind
    )
    judged: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        verdict = str(row.get(column, "")).strip().lower()
        if not verdict:
            continue
        stratum = _stratum(episodes_by_id.get(str(row.get("episode_id", ""))), row)
        if kind is not None and stratum[0] != kind:
            continue
        judged.setdefault(stratum, []).append(verdict == YES)
    if not judged:
        return {}
    # A stratum absent from the episode file (a sheet from another snapshot)
    # still counts, as the size it was sampled at.
    sizes = {h: max(population.get(h, 0), len(v)) for h, v in judged.items()}
    total = sum(sizes.values())
    estimate = variance = 0.0
    for h, verdicts in judged.items():
        n_h, size = len(verdicts), sizes[h]
        p_h = sum(verdicts) / n_h
        weight = size / total
        estimate += weight * p_h
        fpc = 1 - n_h / size
        if n_h > 1:
            variance += weight**2 * fpc * p_h * (1 - p_h) / (n_h - 1)
    n = sum(len(v) for v in judged.values())
    n_eff = estimate * (1 - estimate) / variance if variance > 0 else float(n)
    low, high = wilson_interval(round(estimate * n_eff), round(n_eff)) if n_eff >= 1 else (0.0, 1.0)
    population_total = sum(population.values()) or total
    return {
        "n": n,
        "precision": estimate,
        "ci_low": low,
        "ci_high": high,
        "n_effective": n_eff,
        "coverage": 100.0 * total / population_total,
        "precision_unweighted": sum(sum(v) for v in judged.values()) / n,
    }


def precision_by_signal(
    rows: Iterable[Mapping[str, str]], episodes_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, float | int]]:
    """Per-episode-kind precision, so users can filter on what they trust.

    Keyed by episode kind rather than raw signal: an episode is the labeled
    unit, and its kind is what a downstream user selects on. Weighted over the
    head eras within each kind, for the same reason as ``weighted_precision``.
    """
    rows = list(rows)
    kinds = sorted({_stratum(episodes_by_id.get(str(r.get("episode_id", ""))), r)[0] for r in rows})
    out: dict[str, dict[str, float | int]] = {}
    for kind in kinds:
        if entry := weighted_precision(rows, episodes_by_id, kind):
            out[kind] = entry
    return out


def agreement(
    sheet_a: Sequence[Mapping[str, str]],
    sheet_b: Sequence[Mapping[str, str]],
    columns: Iterable[str],
) -> dict[str, float]:
    """Cohen's kappa per column, matching rows by episode_id."""
    by_id_b = {str(r.get("episode_id", "")): r for r in sheet_b}
    paired = [
        (a, by_id_b[eid]) for a in sheet_a if (eid := str(a.get("episode_id", ""))) in by_id_b
    ]
    if not paired:
        raise ValueError("the two sheets share no episode_id values")
    result: dict[str, float] = {}
    for column in columns:
        left = [normalize(str(a.get(column, ""))) for a, _ in paired]
        right = [normalize(str(b.get(column, ""))) for _, b in paired]
        pairs = [(x, y) for x, y in zip(left, right, strict=True) if x and y]
        if pairs:
            result[column] = cohens_kappa([x for x, _ in pairs], [y for _, y in pairs])
    return result
