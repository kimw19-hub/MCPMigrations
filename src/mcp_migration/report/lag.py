"""Technical lag: how long projects take to adopt a release once it exists.

Lag is measured per release, over a fixed cohort: the repositories already on
an earlier version when the release shipped, and so the ones for which the
release was an upgrade decision. A repository created afterwards that starts on
the new version had no lag to measure, and counting it would make every release
look faster than it was.

Adoption is the first non-merge commit adding the release's marker: its
revision literal for a protocol revision, any v2 SDK signal for the SDK major.

Most of a cohort has not adopted by the end of the window, and dropping those
repositories would make the newest release look fastest simply because only
its quickest adopters have had time to appear. Every repository enters its
cohort on the release date and is followed to one common censoring date, so the
Kaplan-Meier estimate of time to adoption reduces exactly to the cumulative
share of the cohort that has adopted by each day. That share, and the day it
crosses one half if it does, are the statistics that respect the censoring.
Mean and median lag among adopters are reported alongside because they are
familiar, but they describe only those who moved, and they are bounded by how
long each release has been out.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from mcp_migration.mine.signals import (
    PROTOCOL_REVISIONS,
    REVISION_PREFIX,
    V1_SDK_KEYS,
    V2_SDK_KEYS,
)

SDK_V2 = "sdk_v2"


@dataclass(frozen=True)
class Release:
    """One release whose uptake is measured.

    ``prerelease`` is the first public pre-release (a release candidate or beta),
    where one is known. Adopting between it and the release is early adoption and
    counts from day 0. A marker older than that cannot be adoption of a release
    that did not exist yet: in this corpus those are vendored copies of an SDK's
    development branch and test fixtures, so such repositories are excluded
    from the cohort and counted instead.
    """

    key: str
    released: date
    prerelease: date | None = None


# A revision is published on the date it is named for. The v2 SDKs shipped
# with 2026-07-28; their betas and that revision's release candidate are dated
# in config/queries.toml.
RELEASES = (
    Release("2025-03-26", date(2025, 3, 26)),
    Release("2025-06-18", date(2025, 6, 18)),
    Release("2025-11-25", date(2025, 11, 25)),
    Release("2026-07-28", date(2026, 7, 28), prerelease=date(2026, 5, 21)),
    Release(SDK_V2, date(2026, 7, 28), prerelease=date(2026, 6, 29)),
)

# Checkpoints for the share adopted, reported only when the window covers them.
CHECKPOINT_DAYS = (30, 90, 180)


@dataclass
class Firsts:
    """The first date each marker was added in one repository."""

    revisions: dict[str, date]
    v1_sdk: date | None = None
    v2_sdk: date | None = None


def first_adoptions(commits: Iterable[Mapping[str, Any]]) -> Firsts:
    """Reduce one repository's signal commits to the first date of each marker."""
    firsts = Firsts(revisions={})

    def earlier(current: date | None, candidate: date) -> date:
        return candidate if current is None or candidate < current else current

    for commit in commits:
        if commit.get("is_merge"):
            continue
        day = date.fromisoformat(str(commit["date"])[:10])
        added = commit.get("added", {})
        for key, count in added.items():
            if count and key.startswith(REVISION_PREFIX):
                revision = key.removeprefix(REVISION_PREFIX)
                firsts.revisions[revision] = earlier(firsts.revisions.get(revision), day)
        if any(added.get(k) for k in V1_SDK_KEYS):
            firsts.v1_sdk = earlier(firsts.v1_sdk, day)
        if any(added.get(k) for k in V2_SDK_KEYS):
            firsts.v2_sdk = earlier(firsts.v2_sdk, day)
    return firsts


def _before_and_after(release: Release, firsts: Firsts) -> tuple[date | None, date | None]:
    """When this repository was first on an earlier version, and first on this one."""
    if release.key == SDK_V2:
        return firsts.v1_sdk, firsts.v2_sdk
    earlier = PROTOCOL_REVISIONS[: PROTOCOL_REVISIONS.index(release.key)]
    on_earlier = [firsts.revisions[r] for r in earlier if r in firsts.revisions]
    return (min(on_earlier) if on_earlier else None), firsts.revisions.get(release.key)


def lag_profile(release: Release, repos: Iterable[Firsts], censor: date) -> dict[str, Any]:
    """Uptake of one release over its cohort, to the common censoring date."""
    window = (censor - release.released).days
    lags: list[int] = []
    n_cohort = n_prerelease = n_anomalous = 0
    for firsts in repos:
        before, after = _before_and_after(release, firsts)
        if before is None or before >= release.released:
            continue  # not on an earlier version when the release shipped
        if after is not None and release.prerelease and after < release.prerelease:
            n_anomalous += 1
            continue
        n_cohort += 1
        if after is None or after > censor:
            continue  # censored: not adopted within the window
        lag = max(0, (after - release.released).days)
        n_prerelease += after < release.released
        lags.append(lag)
    if not n_cohort:
        return {}

    lags.sort()
    # Cumulative share of the cohort adopted by each day, as (day, percent)
    # steps. With a fixed cohort and one censoring date this is exactly one
    # minus the Kaplan-Meier survival estimate.
    curve: list[tuple[int, float]] = []
    for i, lag in enumerate(lags, start=1):
        if curve and curve[-1][0] == lag:
            curve[-1] = (lag, 100.0 * i / n_cohort)
        else:
            curve.append((lag, 100.0 * i / n_cohort))
    half = next((day for day, pct in curve if pct >= 50.0), None)
    at = {
        day: 100.0 * sum(1 for lag in lags if lag <= day) / n_cohort
        for day in CHECKPOINT_DAYS
        if day <= window
    }
    return {
        "release": release.key,
        "released": release.released.isoformat(),
        "window_days": window,
        "n_cohort": n_cohort,
        "n_adopted": len(lags),
        "n_prerelease": n_prerelease,
        "n_excluded_anomalous": n_anomalous,
        "pct_adopted": 100.0 * len(lags) / n_cohort,
        "pct_by_day": at,
        "days_to_half": half,
        "mean_lag_adopters": statistics.mean(lags) if lags else None,
        "median_lag_adopters": statistics.median(lags) if lags else None,
        "curve": curve,
    }


def technical_lag(repos: Iterable[Firsts], censor: date) -> dict[str, dict[str, Any]]:
    """Lag profile for every release in RELEASES."""
    firsts = list(repos)
    return {r.key: profile for r in RELEASES if (profile := lag_profile(r, firsts, censor))}


def censor_date(heads_dir: Any) -> date | None:
    """The earliest day any repository in the snapshot was mined.

    Repositories were mined over two weeks, so each has seen a different amount
    of history. Censoring all of them at the earliest mining date gives every
    cohort member the same window, at the cost of the adoptions after it.
    """
    from datetime import datetime

    times = [p.stat().st_mtime for p in heads_dir.glob("*.json")]
    return datetime.fromtimestamp(min(times)).date() if times else None


def corpus_seeds(paths: Any) -> set[str]:
    """Repositories seeded from the existing corpora in ``data/external``.

    The cohort lag is measured on. The search queries find repositories by the
    very revision literals whose uptake is being measured, so over the whole
    dataset adoption is selected on: the 30-day share for 2026-07-28 comes out
    more than three times higher than over the corpora. Membership of a corpus
    does not depend on adopting anything, so these repositories are kept
    whether or not a search also found them. Excluding the ones a search found
    would bias the other way, since those are exactly the adopters.
    """
    from mcp_migration.repos import load_seed_file

    external = paths.root / "external"
    found: set[str] = set()
    for source in sorted(external.glob("*")) if external.exists() else []:
        if source.is_file():
            found |= load_seed_file(source)
    return found


def load_lag(paths: Any, only: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Technical lag for the snapshot on disk; empty until something is mined.

    ``only`` restricts it to those repositories (slugs), as for the corpus cohort.
    """
    from mcp_migration.repos import slug_from_dir_name
    from mcp_migration.storage import read_jsonl

    censor = censor_date(paths.heads_dir)
    if censor is None:
        return {}
    files = sorted(paths.commits_dir.glob("*.jsonl"))
    if only is not None:
        files = [f for f in files if slug_from_dir_name(f.stem) in only]
    return technical_lag((first_adoptions(read_jsonl(f)) for f in files), censor)
