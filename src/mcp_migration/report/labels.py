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
    every judgement agrees the variance is zero, and the plain n is used. The
    effective size never exceeds the plain n either: when the only "no" verdicts
    sit in strata with little weight, the variance nearly vanishes and the
    interval would claim more certainty than the episodes actually judged.
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
    n_eff = min(estimate * (1 - estimate) / variance, float(n)) if variance > 0 else float(n)
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


def invalid_rows(rows: Iterable[Mapping[str, str]]) -> list[str]:
    """Every row the labeling app would have refused, as ``episode_id: reason``.

    A sheet edited in a spreadsheet bypasses the app's checks, and a typo such as
    ``dual_eraa`` would otherwise enter kappa as a category of its own.
    """
    from mcp_migration.labeling.sheet import InvalidLabel, validate
    from mcp_migration.report.sampling import CODEBOOK_COLUMNS

    problems: list[str] = []
    for row in rows:
        labels = {c: str(row.get(c) or "") for c in CODEBOOK_COLUMNS if c in row}
        try:
            validate(labels)
        except InvalidLabel as exc:
            problems.append(f"{row.get('episode_id', '?')}: {exc}")
    return problems


def _paired(
    sheet_a: Sequence[Mapping[str, str]], sheet_b: Sequence[Mapping[str, str]]
) -> list[tuple[Mapping[str, str], Mapping[str, str]]]:
    by_id_b = {str(r.get("episode_id", "")): r for r in sheet_b}
    paired = [
        (a, by_id_b[eid]) for a in sheet_a if (eid := str(a.get("episode_id", ""))) in by_id_b
    ]
    if not paired:
        raise ValueError("the two sheets share no episode_id values")
    return paired


def agreement_details(
    sheet_a: Sequence[Mapping[str, str]],
    sheet_b: Sequence[Mapping[str, str]],
    columns: Iterable[str],
) -> dict[str, dict[str, float | int]]:
    """Kappa, raw agreement and the rows behind them, per column.

    ``detector_correct`` is compared wherever both coders judged the episode.
    The other columns describe a genuine migration, so they are compared only
    where both coders said ``yes``, and there a blank is an answer (an
    ambiguous author, no deprecated feature touched), not a missing value.
    Dropping one-sided blanks would discard exactly the disagreements.
    Rows without a ``detector_correct`` value are compared as they are.
    """
    paired = _paired(sheet_a, sheet_b)
    result: dict[str, dict[str, float | int]] = {}
    for column in columns:
        pairs: list[tuple[str, str]] = []
        for a, b in paired:
            x, y = normalize(str(a.get(column, ""))), normalize(str(b.get(column, "")))
            if column == "detector_correct":
                if not (x and y):
                    continue
            else:
                va = normalize(str(a.get("detector_correct", "")))
                vb = normalize(str(b.get("detector_correct", "")))
                if (va or vb) and not (va == vb == YES):
                    continue
            pairs.append((x, y))
        if pairs:
            result[column] = {
                "kappa": cohens_kappa([x for x, _ in pairs], [y for _, y in pairs]),
                "agreement": sum(x == y for x, y in pairs) / len(pairs),
                "n": len(pairs),
            }
    return result


def agreement(
    sheet_a: Sequence[Mapping[str, str]],
    sheet_b: Sequence[Mapping[str, str]],
    columns: Iterable[str],
) -> dict[str, float]:
    """Cohen's kappa per column, matching rows by episode_id; see ``agreement_details``."""
    return {c: float(d["kappa"]) for c, d in agreement_details(sheet_a, sheet_b, columns).items()}


def prelabel_uptake(
    sheet: Sequence[Mapping[str, str]],
    prelabels: Sequence[Mapping[str, str]],
    columns: Iterable[str],
    skip: Iterable[str] = (),
) -> dict[str, Any]:
    """How much of an LLM pre-labelled sheet a verifier kept.

    Assisted labeling is judged by what the human changed, not by agreement
    between two humans who saw the same suggestion. ``skip`` drops episodes the
    verifier coded blind, which were never shown a suggestion to keep.
    """
    columns = list(columns)
    skipped = set(skip)
    by_id = {str(r.get("episode_id", "")): r for r in prelabels}
    rows = [r for r in sheet if str(r.get("episode_id", "")) in by_id]
    rows = [r for r in rows if str(r.get("episode_id", "")) not in skipped]
    changed: dict[str, int] = {}
    episodes: set[str] = set()
    for column in columns:
        n = 0
        for r in rows:
            eid = str(r.get("episode_id", ""))
            if normalize(str(r.get(column, ""))) != normalize(str(by_id[eid].get(column, ""))):
                n += 1
                episodes.add(eid)
        changed[column] = n
    cells = len(rows) * len(columns)
    return {
        "episodes": len(rows),
        "cells": cells,
        "cells_changed": sum(changed.values()),
        "accepted": 1 - sum(changed.values()) / cells if cells else 0.0,
        "episodes_changed": len(episodes),
        "changed_by_column": changed,
        "kappa_vs_prelabels": {
            c: float(d["kappa"]) for c, d in agreement_details(rows, prelabels, columns).items()
        }
        if rows
        else {},
    }


def only(rows: Iterable[Mapping[str, str]], ids: Iterable[str]) -> list[Mapping[str, str]]:
    wanted = set(ids)
    return [r for r in rows if str(r.get("episode_id", "")) in wanted]


def consensus(
    sheet_a: Sequence[Mapping[str, str]], sheet_b: Sequence[Mapping[str, str]]
) -> tuple[list[dict[str, str]], list[str]]:
    """One verdict per episode from two coders, and the episodes they split on.

    Precision from either sheet alone would depend on which file came first. An
    episode counts as genuine only when both coders said so; a split counts as
    ``no`` until it is adjudicated, so the figure is a lower bound, never an
    average of two readings.
    """
    rows: list[dict[str, str]] = []
    split: list[str] = []
    for a, b in _paired(sheet_a, sheet_b):
        x = normalize(str(a.get("detector_correct", "")))
        y = normalize(str(b.get("detector_correct", "")))
        verdict = x if x == y else ("no" if x and y else "")
        if x and y and x != y:
            split.append(str(a.get("episode_id", "")))
        rows.append({**a, "detector_correct": verdict})
    return rows, split


# Triggers in which something outside the project forced the change.
REPAIR_TRIGGERS = ("user_issue", "bot_pr_breakage", "downstream_change")


def coded_profile(rows: Iterable[Mapping[str, str]], kind: str) -> dict[str, Any]:
    """What coders found in the sampled episodes of one kind.

    Counts only episodes judged genuine, since the other columns are blank for
    the rest (see docs/codebook.md).
    """
    sampled = [r for r in rows if str(r.get("kind", "")) == kind]
    genuine = [r for r in sampled if normalize(str(r.get("detector_correct", ""))) == "yes"]
    return {
        "n_sampled": len(sampled),
        "n": len(genuine),
        "end_state": dict(Counter(normalize(str(r.get("end_state", ""))) for r in genuine)),
        "trigger": dict(Counter(normalize(str(r.get("trigger", ""))) for r in genuine)),
    }
