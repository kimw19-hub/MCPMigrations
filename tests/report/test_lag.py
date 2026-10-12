"""Technical lag over a fixed cohort, censored at one date."""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp_migration.report.lag import (
    SDK_V2,
    Firsts,
    Release,
    first_adoptions,
    lag_profile,
)

REL = Release("2025-06-18", date(2025, 6, 18))
CENSOR = date(2025, 9, 16)  # 90 days after REL


def _on(prev: str | None, new: str | None) -> Firsts:
    revs = {}
    if prev:
        revs["2025-03-26"] = date.fromisoformat(prev)
    if new:
        revs["2025-06-18"] = date.fromisoformat(new)
    return Firsts(revisions=revs)


def _commit(day: str, added: dict[str, int], merge: bool = False) -> dict[str, Any]:
    return {"date": f"{day}T12:00:00+00:00", "added": added, "is_merge": merge}


def test_first_adoptions_takes_the_earliest_non_merge_commit() -> None:
    firsts = first_adoptions(
        [
            _commit("2025-07-01", {"revision_literal:2025-06-18": 1}),
            _commit("2025-06-20", {"revision_literal:2025-06-18": 1}, merge=True),
            _commit("2025-06-25", {"revision_literal:2025-06-18": 2, "ts_v1_sdk_pkg": 1}),
            _commit("2026-08-01", {"ts_v2_split_pkg": 1}),
        ]
    )
    assert firsts.revisions == {"2025-06-18": date(2025, 6, 25)}
    assert firsts.v1_sdk == date(2025, 6, 25)
    assert firsts.v2_sdk == date(2026, 8, 1)


def test_only_repositories_on_an_earlier_version_at_release_form_the_cohort() -> None:
    """A repository born on the new revision had no upgrade to make."""
    repos = [
        _on("2025-04-01", "2025-06-28"),  # cohort, adopted on day 10
        _on("2025-04-01", None),  # cohort, never adopted: censored
        _on("2025-07-01", "2025-07-01"),  # started after the release: not cohort
        _on(None, "2025-06-20"),  # never on an earlier revision: not cohort
    ]
    p = lag_profile(REL, repos, CENSOR)
    assert p["n_cohort"] == 2
    assert p["n_adopted"] == 1
    assert p["pct_adopted"] == 50.0
    assert p["days_to_half"] == 10


def test_non_adopters_stay_in_the_denominator() -> None:
    """Dropping them is what would make a recent release look fastest."""
    repos = [_on("2025-04-01", "2025-06-19")] + [_on("2025-04-01", None)] * 3
    p = lag_profile(REL, repos, CENSOR)
    assert p["pct_by_day"][30] == 25.0
    assert p["days_to_half"] is None  # never reached within the window
    assert p["median_lag_adopters"] == 1


def test_adoption_after_the_censoring_date_is_not_counted() -> None:
    p = lag_profile(REL, [_on("2025-04-01", "2025-12-01")], CENSOR)
    assert p["n_adopted"] == 0 and p["n_cohort"] == 1


def test_checkpoints_beyond_the_window_are_omitted() -> None:
    p = lag_profile(REL, [_on("2025-04-01", "2025-06-19")], CENSOR)
    assert set(p["pct_by_day"]) == {30, 90}


def test_early_adoption_counts_from_day_zero_and_older_markers_are_excluded() -> None:
    rel = Release(SDK_V2, date(2026, 7, 28), prerelease=date(2026, 6, 29))
    repos = [
        Firsts({}, v1_sdk=date(2026, 1, 1), v2_sdk=date(2026, 7, 10)),  # beta adopter
        Firsts({}, v1_sdk=date(2026, 1, 1), v2_sdk=date(2025, 3, 1)),  # vendored dev copy
        Firsts({}, v1_sdk=date(2026, 1, 1), v2_sdk=date(2026, 8, 7)),  # day 10
    ]
    p = lag_profile(rel, repos, date(2026, 9, 15))
    assert p["n_cohort"] == 2
    assert p["n_excluded_anomalous"] == 1
    assert p["n_prerelease"] == 1
    assert p["curve"] == [(0, 50.0), (10, 100.0)]
    assert p["mean_lag_adopters"] == 5
