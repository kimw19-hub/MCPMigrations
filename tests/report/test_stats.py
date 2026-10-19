from __future__ import annotations

import json
import re
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest

from mcp_migration.mine.classify import StaticEra
from mcp_migration.mine.signals import PROTOCOL_REVISIONS
from mcp_migration.report.qualitative import ISSUE_THEMES
from mcp_migration.report.stats import (
    _ERA_MACROS,
    _REVISION_MACROS,
    _deferral_outcomes,
    summarize,
    to_latex,
)

EPISODES = [
    {
        "repo": "a/one",
        "kind": "migration",
        "duration_days": 4,
        "author_kinds": ["human_or_unknown"],
        "deprecated_removed": ["dep_sampling", "dep_roots"],
        "revisions_added": ["2026-07-28"],
    },
    {
        "repo": "a/two",
        "kind": "migration",
        "duration_days": 10,
        "author_kinds": ["bot"],
        "deprecated_removed": ["dep_sampling"],
        "revisions_added": ["2026-07-28"],
    },
    {
        "repo": "a/three",
        "kind": "pin_below_2",
        "duration_days": 0,
        "author_kinds": ["ai_agent_assisted"],
        "deprecated_removed": [],
        "revisions_added": [],
    },
]
HEADS = [
    {"static_era": "sdk_v2_modern_features"},
    {"static_era": "sdk_v2_default"},
    {"static_era": "sdk_v1_pinned_below_2"},
]


def test_summarize_counts_kinds_eras_and_deprecations() -> None:
    s = summarize(EPISODES, HEADS, n_search_hits=7, n_seed_repos=5, n_signal_commits=12)
    assert s["n_episodes"] == 3
    assert s["n_mined_repos"] == 3
    assert s["episode_kinds"]["migration"] == 2
    assert s["episode_kinds"]["v2_adoption"] == 0
    assert s["head_eras"]["sdk_v2_modern_features"] == 1
    assert s["deprecated_removed"]["dep_sampling"] == 2
    assert s["deprecated_removed"]["dep_logging"] == 0
    assert s["median_migration_duration_days"] == 7
    assert s["n_bot_episodes"] == 1
    assert s["n_agent_episodes"] == 1
    assert s["revisions_added"] == {"2026-07-28": 2}


def test_summarize_handles_empty_input() -> None:
    s = summarize([], [])
    assert s["n_episodes"] == 0
    assert s["median_migration_duration_days"] == 0
    assert set(s["episode_kinds"]) == {
        "migration",
        "v2_adoption",
        "v1_removal",
        "pin_below_2",
        "revision_change",
    }


def test_to_latex_emits_every_macro_the_paper_declares() -> None:
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12))
    assert "\\def\\nEpisodes{3}" in tex
    assert "\\def\\nMigrationEpisodes{2}" in tex
    assert "\\def\\nDepSampling{2}" in tex
    assert "\\def\\medianDuration{7}" in tex
    assert "\\def\\nHeadPinned{1}" in tex


def test_to_latex_uses_thousands_separators() -> None:
    tex = to_latex(summarize([], [{"static_era": "sdk_v1"}] * 1500))
    assert "\\def\\nMinedRepos{1,500}" in tex


def test_generated_macros_are_declared_in_the_paper_defaults(repo_root: Path) -> None:
    """Every macro `stats` emits must have a placeholder, or the paper won't build."""
    defaults = repo_root / "manuscripts" / "shared" / "numbers-defaults.tex"
    if not defaults.exists():  # pragma: no cover - paper is optional for the package
        pytest.skip("paper/numbers-defaults.tex not present")
    declared = set(re.findall(r"\\def\\(\w+)\{", defaults.read_text(encoding="utf-8")))
    # Include the optional stages, or macros only they emit go unchecked.
    summary = summarize(
        EPISODES,
        HEADS,
        7,
        5,
        12,
        qualitative={
            "pins": {"n": 4, "breakage": 2, "temporary": 1},
            "issues": {"n": 2, **dict.fromkeys(ISSUE_THEMES, 1)},
        },
        label_report={
            "n_labeled": 4,
            "precision_overall": 0.5,
            "kappa": {"detector_correct": 0.9},
            "assisted": {
                "uptake": {"a.csv": {"accepted": 0.98, "cells_changed": 3}},
                "llm_vs_reference": {"detector_correct": {"kappa": 0.95}},
            },
            "blind": {"n": 2, "agreement": {"detector_correct": {"kappa": 0.8}}},
            "precision_source": "adjudicated (round2/adjudicated.csv)",
            "precision_by_kind": {
                "migration": {"n": 5, "precision": 0.9, "ci_low": 0.6, "ci_high": 0.98},
                "v2_adoption": {"n": 5, "precision": 0.8, "ci_low": 0.5, "ci_high": 0.95},
            },
        },
        coded_pins={
            "n_sampled": 3,
            "n": 2,
            "end_state": {"pinned_below_2": 2},
            "trigger": {"downstream_change": 1, "proactive": 1},
        },
    )
    generated = set(re.findall(r"\\def\\(\w+)\{", to_latex(summary)))
    assert generated <= declared, f"undeclared macros: {sorted(generated - declared)}"


def test_every_revision_and_era_has_a_macro() -> None:
    """A revision or era without a macro is silently uncitable in the paper."""
    assert set(_REVISION_MACROS) == set(PROTOCOL_REVISIONS)
    assert set(_ERA_MACROS) == set(StaticEra)


def test_to_latex_emits_revision_counts() -> None:
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12))
    assert "\\def\\nRevJulTwentySix{2}" in tex  # both migrations add 2026-07-28
    assert "\\def\\nRevNovTwentyFour{0}" in tex  # real zero: never observed


def test_era_macros_sum_to_the_mined_repository_count() -> None:
    """The printed era distribution must account for every mined repository."""
    summary = summarize(EPISODES, HEADS, 7, 5, 12)
    assert sum(summary["head_eras"].values()) == summary["n_mined_repos"]


def test_deferral_counts_a_repository_that_pinned_then_moved() -> None:
    """Deferral is only interesting as an outcome, so order matters."""
    episodes = [
        {"repo": "a/moved", "kind": "pin_below_2", "start": "2026-07-01"},
        {"repo": "a/moved", "kind": "migration", "start": "2026-08-01"},
        {"repo": "b/stayed", "kind": "pin_below_2", "start": "2026-07-01"},
        {"repo": "c/never-pinned", "kind": "migration", "start": "2026-08-01"},
    ]
    assert _deferral_outcomes(episodes) == (2, 1)


def test_a_migration_before_the_pin_does_not_count_as_moving() -> None:
    """Migrating and later pinning is a retreat, not a deferral resolved."""
    episodes = [
        {"repo": "a/retreated", "kind": "migration", "start": "2026-06-01"},
        {"repo": "a/retreated", "kind": "pin_below_2", "start": "2026-08-01"},
    ]
    assert _deferral_outcomes(episodes) == (1, 0)


def test_to_latex_omits_stages_that_have_not_run() -> None:
    """An empty dataset must render as [??], not as a zero that reads as a finding."""
    tex = to_latex(summarize([], []))
    assert "\\def\\nEpisodes" not in tex
    assert "\\def\\nMigrationEpisodes" not in tex
    assert "\\def\\nHeadPinned" not in tex
    assert "\\def\\medianDuration" not in tex
    assert "\\def\\snapshotDate" in tex  # always known


def test_to_latex_keeps_genuine_zeros_once_episodes_exist() -> None:
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12))
    assert "\\def\\nDepLogging{0}" in tex  # real zero: no logging removals observed
    assert "\\def\\nVTwoAdoption{0}" in tex


def test_stats_counts_hits_from_per_query_files(tmp_path: Path) -> None:
    """`stats` must see search hits after the per-query files are merged."""
    from mcp_migration.config import Paths

    paths = Paths(tmp_path)
    paths.search_dir.mkdir(parents=True)
    (paths.search_dir / "q1.jsonl").write_text('{"repo":"a/b"}\n', encoding="utf-8")
    (paths.search_dir / "q2.jsonl").write_text('{"repo":"c/d"}\n', encoding="utf-8")
    from mcp_migration.report.stats import load_all

    # Counted from the per-query files; no merged file exists after an interrupt.
    assert not paths.search_hits.exists()
    assert load_all(paths)["n_search_hits"] == 2


def test_write_jsonl_leaves_no_file_when_the_source_fails(tmp_path: Path) -> None:
    """A crashed stage must not leave a partial file that resume mistakes for done."""
    from mcp_migration.storage import write_jsonl

    def records() -> Iterator[dict[str, int]]:
        yield {"a": 1}
        raise RuntimeError("network died mid-query")

    out = tmp_path / "hits.jsonl"
    with pytest.raises(RuntimeError):
        write_jsonl(out, records())
    assert not out.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_write_jsonl_replaces_atomically(tmp_path: Path) -> None:
    from mcp_migration.storage import write_jsonl

    out = tmp_path / "hits.jsonl"
    assert write_jsonl(out, iter([{"a": 1}, {"a": 2}])) == 2
    assert write_jsonl(out, iter([{"a": 3}])) == 1
    assert out.read_text().strip() == '{"a": 3}'


def test_gap_sensitivity_reports_a_row_per_gap(tmp_path: Path) -> None:
    """Episode boundaries are a free parameter; the paper reports its effect."""
    from mcp_migration.config import Paths
    from mcp_migration.report.stats import gap_sensitivity

    paths = Paths(tmp_path)
    paths.commits_dir.mkdir(parents=True)
    # Two migration commits 20 days apart: one episode at gap 30, two at gap 7.
    rows = [
        {
            "repo": "a/b",
            "sha": sha,
            "date": f"2026-08-{day:02d}T00:00:00+00:00",
            "author_kind": "human_or_unknown",
            "is_merge": False,
            "lines_added": 1,
            "lines_deleted": 1,
            "added": {"py_v2_mcpserver_import": 1},
            "removed": {"py_v1_fastmcp_import": 1},
        }
        for sha, day in (("aa", 1), ("bb", 21))
    ]
    (paths.commits_dir / "a__b.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    by_gap = {r["max_gap_days"]: r for r in gap_sensitivity(paths, gaps=(7, 30))}
    assert by_gap[7]["n_episodes"] == 2
    assert by_gap[30]["n_episodes"] == 1
    assert by_gap[30]["n_migration"] == 1


# The shared EPISODES fixture omits fields the stats tests do not need. viz
# builds a published payload from real records, so it is given real ones.
VIZ_EPISODES = [
    {
        **e,
        "start": f"2026-0{7 + i % 2}-1{i}T00:00:00+00:00",
        "head_static_era": era,
        "scope": "sdk_and_protocol",
        "replacements": [],
        "shas": ["a" * 40],
    }
    for i, (e, era) in enumerate(
        zip(EPISODES, ["sdk_v2_default", "sdk_v1", "mixed_v1_v2"], strict=True)
    )
]


def test_viz_summary_covers_every_kind_and_era() -> None:
    """A kind missing from the payload is a chart series that silently vanishes."""
    from mcp_migration.mine.classify import StaticEra
    from mcp_migration.report.stats import EPISODE_KINDS
    from mcp_migration.report.viz import summary as viz_summary

    s = viz_summary(VIZ_EPISODES)
    assert set(s["kinds"]) == set(EPISODE_KINDS)
    assert set(s["eras"]) == {e.value for e in StaticEra}
    assert len(s["byKind"]["migration"]) == len(s["months"])
    assert len(s["authors"]["total"]) == len(s["months"])
    for series in s["revisions"].values():
        assert len(series) == len(s["months"])


def test_viz_revision_series_is_cumulative() -> None:
    from mcp_migration.report.viz import summary as viz_summary

    for series in viz_summary(VIZ_EPISODES)["revisions"].values():
        assert all(b >= a for a, b in pairwise(series))


def test_inlining_escapes_a_closing_script_tag() -> None:
    """A literal </script> in the data would end the block early and break the page."""
    from mcp_migration.report.viz import inline_summary

    page = '<script id="summary-data" type="application/json">{}</script><p>after</p>'
    out = inline_summary(page, {"note": "</script> inside"})
    assert "</script> inside" not in out
    assert out.count("</script>") == 1
    assert out.endswith("<p>after</p>")


def test_signal_diffs_are_index_encoded_against_the_vocabulary(tmp_path: Path) -> None:
    """The payload stores indices; a drifting vocabulary would mislabel every diff."""
    from mcp_migration.report.viz import episode_rows, signal_vocabulary

    commits = tmp_path / "commits"
    commits.mkdir()
    (commits / "a__b.jsonl").write_text(
        json.dumps(
            {
                "sha": "f" * 40,
                "added": {"py_v2_mcpserver_import": 1},
                "removed": {"py_v1_fastmcp_import": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    episode = {
        "episode_id": "a/b#0",
        "repo": "a/b",
        "kind": "migration",
        "start": "2026-08-01T00:00:00+00:00",
        "shas": ["f" * 40],
        "duration_days": 0,
    }
    row = episode_rows([episode], commits)[0]
    vocabulary = signal_vocabulary()
    assert [vocabulary[i] for i in row["add"]] == ["py_v2_mcpserver_import"]
    assert [vocabulary[i] for i in row["del"]] == ["py_v1_fastmcp_import"]


def test_a_single_commit_episode_carries_no_range() -> None:
    """With one commit there is nothing to compare, so the page links the commit."""
    from mcp_migration.report.viz import episode_rows

    episode = {
        "episode_id": "a/b#0",
        "repo": "a/b",
        "kind": "migration",
        "start": "2026-08-01T00:00:00+00:00",
        "shas": ["a" * 40],
        "duration_days": 0,
    }
    row = episode_rows([episode])[0]
    assert row["f"] == "a" * 10
    assert "l" not in row and "b" not in row


def test_abbreviated_hashes_stay_long_enough_to_resolve() -> None:
    from mcp_migration.report.viz import SHA_PREFIX

    assert SHA_PREFIX >= 7, "git needs at least 7 characters to disambiguate"


def test_coded_text_reaches_the_paper_macros() -> None:
    """The counts are useless if `summarize` drops them before `to_latex`."""
    qualitative = {
        "pins": {"n": 448, "breakage": 263, "temporary": 1},
        "issues": {"n": 70, "names_rename": 65, "import_error": 58, "proposes_pin": 52},
    }
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12, qualitative=qualitative))
    assert "\\def\\nPinMessages{448}" in tex
    assert "\\def\\pctPinBreakage{58.7}" in tex
    assert "\\def\\nPinTemporary{1}" in tex
    assert "\\def\\nIssuesCoded{70}" in tex
    assert "\\def\\pctIssueRename{92.9}" in tex


def test_stats_passes_coded_text_from_disk_through_to_the_macros(tmp_path: Path) -> None:
    """Regression: `cmd_stats` once loaded the counts and then never used them."""
    import argparse

    from mcp_migration.cli import cmd_stats
    from mcp_migration.config import Paths
    from mcp_migration.storage import write_json

    paths = Paths(tmp_path)
    write_json(paths.qualitative, {"pins": {"n": 10, "breakage": 5, "temporary": 0}})
    tex = tmp_path / "numbers.tex"
    args = argparse.Namespace(out=tmp_path / "summary.json", tex=tex)
    assert cmd_stats(args, paths) == 0
    assert "\\def\\nPinMessages{10}" in tex.read_text(encoding="utf-8")


def test_size_profile_separates_what_the_cap_keeps_from_what_it_costs() -> None:
    """The cap is defended in both papers as cheap; that has to be measurable."""
    from mcp_migration.report.stats import size_profile

    mb = 1024
    sizes = [1 * mb, 2 * mb, 3 * mb, 500 * mb]  # one outlier dominates the total
    p = size_profile(sizes, cap_kb=100_000)
    assert p["pct_under_cap"] == 75.0
    assert p["median_mb"] == 2.5
    assert p["gb_under_cap"] < p["gb_uncapped"]
    assert round(p["max_gb"], 3) == round(500 / 1024, 3)


def test_size_macros_are_absent_rather_than_zero_without_metadata() -> None:
    """`stats` runs before `metadata` does; the paper must fall back, not print 0."""
    from mcp_migration.report.stats import size_profile

    assert size_profile([]) == {}
    assert "pctUnderSizeCap" not in to_latex(summarize(EPISODES, HEADS, 7, 5, 12))


def test_the_stated_cap_is_the_cap_the_figures_use() -> None:
    """The papers said 100 MB for weeks after the corpus was built at 2 GB."""
    mb = 1024
    sizes = [1 * mb, 3 * mb, 1500 * mb, 2500 * mb]
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12, repo_sizes=sizes, cap_kb=2_000_000))
    assert "\\def\\gbSizeCap{2}" in tex
    assert "\\def\\pctUnderSizeCap{75.0}" in tex


def test_lag_macros_report_shares_of_the_cohort_and_only_checkpoints_reached() -> None:
    from datetime import date

    from mcp_migration.report.lag import Firsts, Release, lag_profile

    rel = Release("2026-07-28", date(2026, 7, 28))
    repos = [Firsts({"2025-11-25": date(2026, 1, 1), "2026-07-28": date(2026, 8, 7)})] + [
        Firsts({"2025-11-25": date(2026, 1, 1)})
    ] * 3
    lag = {"2026-07-28": lag_profile(rel, repos, date(2026, 9, 15))}
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12, lag=lag))
    assert "\\def\\nLagCohortJulTwentySix{4}" in tex
    assert "\\def\\pctLagThirtyJulTwentySix{25.0}" in tex
    assert "pctLagNinetyJulTwentySix" not in tex  # a 49-day window cannot say
    assert "\\def\\lagMedianJulTwentySix{10}" in tex
    assert "\\def\\lagCensorDate{2026-09-15}" in tex


def test_harvest_outcomes_become_counts_and_the_budget_actually_applied() -> None:
    outcomes = [
        {"repo": "a__x", "reason": "unavailable", "detail": "clone failed"},
        {"repo": "a__y", "reason": "timeout", "detail": "timeout: exceeded 1800s after 2 signals"},
        {"repo": "a__z", "reason": "timeout", "detail": "timeout: exceeded 1800s after 0 signals"},
    ]
    tex = to_latex(summarize(EPISODES, HEADS, 7, 5, 12, harvest_outcomes=outcomes))
    assert "\\def\\nHarvestUnavailable{1}" in tex
    assert "\\def\\nHarvestTimeout{2}" in tex
    assert "\\def\\minHarvestBudget{30}" in tex


def test_measurement_macros_carry_intervals_and_leave_gaps_unfilled() -> None:
    from mcp_migration.report.stats import _measurement_macros

    km = {"n": 800, "events": 3, "by_day": {30: {"pct": 0.3, "ci_low": 0.0, "ci_high": 0.8}}}
    m = {
        "deferral": {
            "pinned": km,
            "controls": None,
            "scopes": {"deferral": 5, "v2_same_commit": 2, "v2_already_present": 1},
        },
        "protocol": {
            "typescript": {
                "n": 10,
                "resolved": 4,
                "pct_modern": 75.0,
                "pct_legacy": None,
                "pct_new_features": 20.0,
            }
        },
    }
    out = _measurement_macros(m)
    assert out["pctDefPinned"] == "0.3" and out["pctDefPinnedHigh"] == "0.8"
    assert "nDefUnpinned" not in out  # absent cohort renders as [??], not 0
    assert out["nCeilCommits"] == "8" and out["nCeilDeferral"] == "5"
    assert out["nCeilSameCommit"] == "2" and out["nCeilAlready"] == "1"
    assert out["nTsResolved"] == "4" and out["pctTsModern"] == "75.0"
    assert "pctTsLegacy" not in out


def test_held_out_precision_needs_a_blind_subset_and_an_adjudicated_sheet() -> None:
    """Round 1 has no blind subset, so it must never fill the held-out macros."""
    by_kind = {"migration": {"n": 5, "precision": 0.9, "ci_low": 0.6, "ci_high": 0.98}}
    base = {"n_labeled": 5, "precision_overall": 0.9, "precision_by_kind": by_kind}
    adjudicated = {"precision_source": "adjudicated (x.csv)"}
    blind = {"blind": {"n": 2}}
    for report, filled in (
        ({**base, **adjudicated}, False),  # round 1: adjudicated, no blind subset
        ({**base, **blind, "precision_source": "consensus"}, False),  # before adjudication
        ({**base, **blind, **adjudicated}, True),
    ):
        tex = to_latex(summarize([], [], label_report=report))
        assert ("\\def\\precHeldOutMigration{0.90}" in tex) is filled
