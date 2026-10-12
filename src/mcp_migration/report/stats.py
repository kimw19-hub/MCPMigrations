"""Descriptive statistics for the manuscript.

Produces a JSON summary and a LaTeX macro file, so that every figure cited in
the paper is regenerated from the data rather than typed by hand. Macro names
must match those declared in ``paper/numbers-defaults.tex``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp_migration.collect.filtering import DEFAULT_MAX_DISK_KB
from mcp_migration.mine.classify import AuthorKind, StaticEra
from mcp_migration.mine.signals import DEPRECATED_KEYS
from mcp_migration.report.labels import wilson_interval

EPISODE_KINDS = ("migration", "v2_adoption", "v1_removal", "pin_below_2", "revision_change")
# Gaps reported for the episode-boundary sensitivity analysis in the paper.
SENSITIVITY_GAPS = (3, 7, 14, 30, 60)

# Macro name -> the statistic it reports. Keep in sync with numbers-defaults.tex.
_KIND_MACROS = {
    "migration": "nMigrationEpisodes",
    "v2_adoption": "nVTwoAdoption",
    "v1_removal": "nVOneRemoval",
    "pin_below_2": "nPinBelowTwo",
    "revision_change": "nRevisionChange",
}
# Every era, so the distribution the paper prints sums to nMinedRepos rather
# than silently dropping the repositories that use no official SDK.
_ERA_MACROS = {
    StaticEra.SDK_V2_MODERN_FEATURES: "nHeadModern",
    StaticEra.SDK_V2_DEFAULT: "nHeadVTwoDefault",
    StaticEra.SDK_V1_PINNED_BELOW_2: "nHeadPinned",
    StaticEra.SDK_V1: "nHeadVOne",
    StaticEra.MIXED_V1_V2: "nHeadMixed",
    StaticEra.STANDALONE_FASTMCP: "nHeadFastmcp",
    StaticEra.NO_OFFICIAL_SDK: "nHeadNoSdk",
}
# Keyed by revision literal rather than position, so adding a sixth revision to
# PROTOCOL_REVISIONS is a deliberate edit here and in numbers-defaults.tex.
_REVISION_MACROS = {
    "2024-11-05": "nRevNovTwentyFour",
    "2025-03-26": "nRevMarTwentyFive",
    "2025-06-18": "nRevJunTwentyFive",
    "2025-11-25": "nRevNovTwentyFive",
    "2026-07-28": "nRevJulTwentySix",
}
# Structural replacements, keyed by the label episodes.py emits.
_REPLACEMENT_MACROS = {
    "mrtr_input_required": "nReplSampling",
    "subscriptions_listen": "nReplSubscriptions",
    "server_discover": "nReplDiscover",
}
# Technical lag, keyed by release as report.lag names them.
_LAG_MACROS = {
    "2025-03-26": "MarTwentyFive",
    "2025-06-18": "JunTwentyFive",
    "2025-11-25": "NovTwentyFive",
    "2026-07-28": "JulTwentySix",
    "sdk_v2": "SdkVTwo",
}
_DEP_MACROS = {
    "dep_sampling": "nDepSampling",
    "dep_roots": "nDepRoots",
    "dep_logging": "nDepLogging",
    "dep_resource_subscribe": "nDepSubscribe",
}


def _fmt(value: int | float) -> str:
    """Format a number for LaTeX: thousands separators, one decimal for floats."""
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}"


def _pct(value: int, total: int) -> str:
    """Format a share for LaTeX. The table supplies the % sign, not the macro."""
    return f"{100.0 * value / total:.1f}" if total else "0.0"


def summarize(
    episodes: Sequence[Mapping[str, Any]],
    heads: Sequence[Mapping[str, Any]],
    n_search_hits: int = 0,
    n_seed_repos: int = 0,
    n_signal_commits: int = 0,
    n_resolved_repos: int = 0,
    n_clone_candidates: int = 0,
    exclusions: Mapping[str, int] | None = None,
    gap_rows: Sequence[Mapping[str, Any]] | None = None,
    label_report: Mapping[str, Any] | None = None,
    qualitative: Mapping[str, Any] | None = None,
    repo_sizes: Sequence[int] | None = None,
    exclusion: Mapping[str, float] | None = None,
    cap_kb: int = DEFAULT_MAX_DISK_KB,
    lag: Mapping[str, Any] | None = None,
    lag_all: Mapping[str, Any] | None = None,
    lag_repos: int = 0,
    harvest_outcomes: Sequence[Mapping[str, Any]] | None = None,
    n_search_queries: int = 0,
    n_sample_size: int = 0,
) -> dict[str, Any]:
    """Compute every statistic the manuscript cites."""
    kinds = Counter(str(e["kind"]) for e in episodes)
    eras = Counter(str(h.get("static_era", "")) for h in heads)
    deprecated = Counter(
        key for e in episodes for key in e.get("deprecated_removed", []) if key in DEPRECATED_KEYS
    )
    revisions: Counter[str] = Counter()
    for episode in episodes:
        for revision in episode.get("revisions_added", []):
            revisions[str(revision)] += 1

    # Where migration episodes actually land. Adopting the v2 SDK is not the
    # same as reaching the new protocol era, and the gap is the point.
    migration_ends: Counter[str] = Counter(
        str(e.get("head_static_era") or "") for e in episodes if e["kind"] == "migration"
    )
    replacements: Counter[str] = Counter()
    for episode in episodes:
        for label in episode.get("replacements", []):
            replacements[str(label)] += 1
    durations = [int(e["duration_days"]) for e in episodes if e["kind"] == "migration"]
    author_kinds = [set(e.get("author_kinds", [])) for e in episodes]
    pinned, pinned_moved = _deferral_outcomes(episodes)

    return {
        "snapshot_date": datetime.now(UTC).date().isoformat(),
        "n_search_hits": n_search_hits,
        "n_seed_repos": n_seed_repos,
        "n_signal_commits": n_signal_commits,
        "n_resolved_repos": n_resolved_repos,
        "n_clone_candidates": n_clone_candidates,
        "exclusions": dict(exclusions or {}),
        "gap_sensitivity": list(gap_rows or []),
        "labels": dict(label_report or {}),
        "n_mined_repos": len(heads),
        "n_episodes": len(episodes),
        "episode_kinds": {k: kinds.get(k, 0) for k in EPISODE_KINDS},
        "head_eras": {era.value: eras.get(era.value, 0) for era in StaticEra},
        "deprecated_removed": {k: deprecated.get(k, 0) for k in DEPRECATED_KEYS},
        "revisions_added": dict(sorted(revisions.items())),
        "median_migration_duration_days": statistics.median(durations) if durations else 0,
        "n_bot_episodes": sum(AuthorKind.BOT in ks for ks in author_kinds),
        "n_agent_episodes": sum(AuthorKind.AI_AGENT_ASSISTED in ks for ks in author_kinds),
        "n_pinned_repos": pinned,
        "n_pinned_later_moved": pinned_moved,
        "replacements": dict(sorted(replacements.items())),
        "migration_end_states": {era.value: migration_ends.get(era.value, 0) for era in StaticEra},
        "qualitative": dict(qualitative or {}),
        "repo_size": size_profile(repo_sizes or [], cap_kb),
        "exclusion": dict(exclusion or {}),
        # Measured on the corpus-seeded cohort; the whole-dataset version is kept
        # only to show how far the search inflates it (see report.lag).
        "technical_lag": dict(lag or {}),
        "technical_lag_all": dict(lag_all or {}),
        "technical_lag_repos": lag_repos,
        "harvest": harvest_profile(harvest_outcomes or []),
        "n_search_queries": n_search_queries,
        "n_sample_size": n_sample_size,
    }


# The two ends of the query set: one matches a revision date anywhere in a
# commit and will hit any large codebase that mentions it, the other matches the
# SDK identifier rename and only fires on code that uses the SDK.
BROAD_QUERY = "commit_revision_2025_06"
SDK_QUERY = "commit_fastmcp_to_mcpserver"


def harvest_profile(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Clone candidates the harvest attempted and did not mine, by reason.

    The budget is read back from the timeout records themselves ("exceeded
    1800s ..."), so the paper states the budget the run actually applied.
    """
    import re

    reasons = Counter(str(o.get("reason", "")) for o in outcomes)
    budgets = [
        int(m.group(1))
        for o in outcomes
        if o.get("reason") == "timeout"
        and (m := re.search(r"exceeded (\d+)s", str(o.get("detail"))))
    ]
    profile = {"unavailable": reasons.get("unavailable", 0), "timeout": reasons.get("timeout", 0)}
    if budgets:
        profile["budget_min"] = max(budgets) // 60
    return profile


def exclusion_profile(
    metadata: Sequence[Mapping[str, Any]],
    repo_queries: Mapping[str, set[str]],
    cap_kb: int,
) -> dict[str, float]:
    """How the repositories above a size cap differ from those below it.

    The cap was defended as a storage measure, which invited the reading that
    it drops large mature servers. Provenance says otherwise, and the paper
    should argue from provenance rather than from bytes -- so the comparison
    is computed here rather than asserted.
    """
    live = [m for m in metadata if not (m.get("archived") or m.get("fork") or m.get("empty"))]
    over = [m for m in live if int(m.get("disk_usage_kb") or 0) > cap_kb]
    under = [m for m in live if int(m.get("disk_usage_kb") or 0) <= cap_kb]
    if not over or not under:
        return {}

    def share(group: Sequence[Mapping[str, Any]], query: str) -> float:
        hit = sum(1 for m in group if query in repo_queries.get(str(m["repo"]), ()))
        return 100.0 * hit / len(group)

    return {
        "n_over": len(over),
        "gb_over": sum(int(m["disk_usage_kb"]) for m in over) / (1024 * 1024),
        "pct_over_broad": share(over, BROAD_QUERY),
        "pct_under_broad": share(under, BROAD_QUERY),
        "pct_over_sdk": share(over, SDK_QUERY),
        "pct_under_sdk": share(under, SDK_QUERY),
        "n_over_named_mcp": sum(1 for m in over if "mcp" in str(m["repo"]).lower()),
    }


def size_profile(sizes_kb: Sequence[int], cap_kb: int = DEFAULT_MAX_DISK_KB) -> dict[str, float]:
    """What the disk-usage cap keeps, and what dropping it would cost.

    The paper argues the cap is a tractability cut rather than a correctness
    one, which is only checkable with the distribution in hand. Computed rather
    than quoted: the share under the cap was written into the manuscript by
    hand once and was stale by 0.1 point the next time the corpus grew.
    """
    if not sizes_kb:
        return {}
    kept = [s for s in sizes_kb if s <= cap_kb]
    gb = 1024 * 1024
    return {
        "n": len(sizes_kb),
        "cap_mb": cap_kb / 1000,
        "pct_under_cap": 100.0 * len(kept) / len(sizes_kb),
        "median_mb": statistics.median(sizes_kb) / 1024,
        "mean_mb": statistics.mean(sizes_kb) / 1024,
        "gb_under_cap": sum(kept) / gb,
        "gb_uncapped": sum(sizes_kb) / gb,
        "max_gb": max(sizes_kb) / gb,
    }


def _deferral_outcomes(episodes: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Repositories that pinned below the major, and how many later moved.

    A repository counts as having moved if any episode after its first pin is a
    migration or a v2 adoption. This is what makes deferral observable as an
    outcome rather than merely as a state.
    """
    by_repo: dict[str, list[Mapping[str, Any]]] = {}
    for episode in episodes:
        by_repo.setdefault(str(episode.get("repo", "")), []).append(episode)
    pinned = moved = 0
    for group in by_repo.values():
        ordered = sorted(group, key=lambda e: str(e.get("start", "")))
        first_pin = next((i for i, e in enumerate(ordered) if e["kind"] == "pin_below_2"), None)
        if first_pin is None:
            continue
        pinned += 1
        if any(e["kind"] in ("migration", "v2_adoption") for e in ordered[first_pin + 1 :]):
            moved += 1
    return pinned, moved


def to_latex(summary: Mapping[str, Any]) -> str:
    """Render the summary as ``\\def`` macros overriding numbers-defaults.tex.

    A stage that has not run yet is omitted rather than emitted as zero, so the
    manuscript shows ``[??]`` instead of a zero that reads like a finding.
    """
    macros: dict[str, str] = {"snapshotDate": str(summary["snapshot_date"])}

    # Stage counts: a zero here means the stage has not run.
    for macro, key in (
        ("nSearchHits", "n_search_hits"),
        ("nSeedRepos", "n_seed_repos"),
        ("nSignalCommits", "n_signal_commits"),
        ("nResolvedRepos", "n_resolved_repos"),
        ("nCloneCandidates", "n_clone_candidates"),
        ("nMinedRepos", "n_mined_repos"),
        ("nEpisodes", "n_episodes"),
    ):
        if summary[key]:
            macros[macro] = _fmt(summary[key])

    if summary["n_episodes"]:
        macros["medianDuration"] = _fmt(summary["median_migration_duration_days"])
        macros["nBotEpisodes"] = _fmt(summary["n_bot_episodes"])
        macros["nAgentEpisodes"] = _fmt(summary["n_agent_episodes"])
        for kind, macro in _KIND_MACROS.items():
            count = int(summary["episode_kinds"].get(kind, 0))
            macros[macro] = _fmt(count)
            macros["pct" + macro[1:]] = _pct(count, summary["n_episodes"])
        # Migration counts under different clustering gaps, as a spread over the
        # largest. The paper cites this to show the migration population is not
        # an artifact of the 30-day boundary.
        if summary["n_pinned_repos"]:
            macros["nPinnedRepos"] = _fmt(summary["n_pinned_repos"])
            macros["pctPinnedMoved"] = _pct(
                summary["n_pinned_later_moved"], summary["n_pinned_repos"]
            )
        by_gap: list[int] = [int(row["n_migration"]) for row in summary["gap_sensitivity"]]
        widest = max(by_gap, default=0)
        if widest:
            macros["migrationGapSpread"] = f"{100.0 * (widest - min(by_gap)) / widest:.0f}"
        for key, macro in _DEP_MACROS.items():
            macros[macro] = _fmt(summary["deprecated_removed"].get(key, 0))
        for revision, macro in _REVISION_MACROS.items():
            macros[macro] = _fmt(summary["revisions_added"].get(revision, 0))
        for label, macro in _REPLACEMENT_MACROS.items():
            macros[macro] = _fmt(summary["replacements"].get(label, 0))
        ends = summary["migration_end_states"]
        total_migrations = sum(ends.values())
        if total_migrations:
            modern = int(ends.get(StaticEra.SDK_V2_MODERN_FEATURES.value, 0))
            # Both states still answer the legacy handshake, so neither has
            # finished the protocol migration whatever the SDK says.
            dual = int(ends.get(StaticEra.SDK_V2_DEFAULT.value, 0)) + int(
                ends.get(StaticEra.MIXED_V1_V2.value, 0)
            )
            macros["nMigrationModern"] = _fmt(modern)
            macros["pctMigrationModern"] = _pct(modern, total_migrations)
            macros["nMigrationDual"] = _fmt(dual)
            macros["pctMigrationDual"] = _pct(dual, total_migrations)

    if summary["n_mined_repos"]:
        for era, macro in _ERA_MACROS.items():
            count = int(summary["head_eras"].get(era.value, 0))
            macros[macro] = _fmt(count)
            macros["pct" + macro[1:]] = _pct(count, summary["n_mined_repos"])

    # Quality figures exist only once the sample has actually been coded.
    labels = summary.get("labels") or {}
    if labels.get("n_labeled"):
        macros["nSampled"] = _fmt(labels["n_labeled"])
        macros["precisionOverall"] = f"{float(labels['precision_overall']):.2f}"
        # An interval, because the reviewer of a precision figure wants to know
        # how much sample stands behind it.
        # The interval `labels` computed for the stratified estimate; a plain
        # Wilson interval on the row count only for reports made before it.
        ci = labels.get("precision_ci") or [None, None]
        if ci[0] is not None and ci[1] is not None:
            low, high = float(ci[0]), float(ci[1])
        else:
            low, high = wilson_interval(
                round(float(labels["precision_overall"]) * int(labels["n_labeled"])),
                int(labels["n_labeled"]),
            )
        macros["precisionLow"] = f"{low:.2f}"
        macros["precisionHigh"] = f"{high:.2f}"
        by_kind = labels.get("precision_by_kind") or {}
        for kind, macro in _KIND_MACROS.items():
            entry = by_kind.get(kind)
            if entry:
                macros["prec" + macro[1:]] = f"{float(entry['precision']):.2f}"
        kappa = (labels.get("kappa") or {}).get("detector_correct")
        if kappa is not None:
            macros["kappaOverall"] = f"{float(kappa):.2f}"

    # The size cap: what it keeps and what lifting it would cost.
    size = summary.get("repo_size") or {}
    if size:
        # The cap itself, so the prose cannot state one cap and compute another.
        macros["gbSizeCap"] = _fmt(size["cap_mb"] / 1000)
        macros["pctUnderSizeCap"] = f"{size['pct_under_cap']:.1f}"
        macros["mbMedianRepo"] = f"{size['median_mb']:.1f}"
        macros["mbMeanRepo"] = f"{size['mean_mb']:.0f}"
        macros["gbUnderSizeCap"] = f"{size['gb_under_cap']:.0f}"
        macros["tbUncapped"] = f"{size['gb_uncapped'] / 1024:.1f}"
        macros["gbLargestRepo"] = f"{size['max_gb']:.0f}"

    # Why the largest repositories are dropped: provenance, not bytes.
    excl = summary.get("exclusion") or {}
    if excl:
        macros["nExcludedLarge"] = f"{int(excl['n_over']):,}"
        macros["gbExcludedLarge"] = f"{excl['gb_over']:.0f}"
        macros["pctLargeBroadQuery"] = f"{excl['pct_over_broad']:.0f}"
        macros["pctSmallBroadQuery"] = f"{excl['pct_under_broad']:.0f}"
        macros["pctLargeSdkQuery"] = f"{excl['pct_over_sdk']:.0f}"
        macros["pctSmallSdkQuery"] = f"{excl['pct_under_sdk']:.0f}"
        macros["nLargeNamedMcp"] = f"{int(excl['n_over_named_mcp']):,}"

    # Technical lag. Shares of the cohort, which keep the non-adopters in the
    # denominator; mean and median describe adopters only.
    lag = summary.get("technical_lag") or {}
    for key, suffix in _LAG_MACROS.items():
        entry = lag.get(key)
        if not entry:
            continue
        macros["nLagCohort" + suffix] = _fmt(int(entry["n_cohort"]))
        macros["pctLagAdopted" + suffix] = f"{entry['pct_adopted']:.1f}"
        for day, word in ((30, "Thirty"), (90, "Ninety")):
            share = (entry.get("pct_by_day") or {}).get(
                day, (entry.get("pct_by_day") or {}).get(str(day))
            )
            if share is not None:
                macros[f"pctLag{word}" + suffix] = f"{share:.1f}"
        if entry.get("median_lag_adopters") is not None:
            macros["lagMedian" + suffix] = _fmt(float(entry["median_lag_adopters"]))
            macros["lagMean" + suffix] = f"{float(entry['mean_lag_adopters']):.0f}"
    # Drawn, as against coded: nSampled is the count actually labeled.
    for macro, key in (("nSearchQueries", "n_search_queries"), ("nSampleSize", "n_sample_size")):
        if summary.get(key):
            macros[macro] = _fmt(int(summary[key]))
    harvest = summary.get("harvest") or {}
    if harvest.get("unavailable") or harvest.get("timeout"):
        macros["nHarvestUnavailable"] = _fmt(int(harvest.get("unavailable", 0)))
        macros["nHarvestTimeout"] = _fmt(int(harvest.get("timeout", 0)))
    if harvest.get("budget_min"):
        macros["minHarvestBudget"] = _fmt(int(harvest["budget_min"]))
    if summary.get("technical_lag_repos"):
        macros["nLagRepos"] = _fmt(int(summary["technical_lag_repos"]))
    if lag:
        first = next(iter(lag.values()))
        macros["lagCensorDate"] = (
            date.fromisoformat(str(first["released"])) + timedelta(days=int(first["window_days"]))
        ).isoformat()
    inflated = (summary.get("technical_lag_all") or {}).get("2026-07-28") or {}
    share_all = (inflated.get("pct_by_day") or {}).get(
        30, (inflated.get("pct_by_day") or {}).get("30")
    )
    if share_all is not None:
        macros["pctLagThirtyAllJulTwentySix"] = f"{share_all:.1f}"

    # Coded developer text: the counts behind the qualitative paragraph in RQ1.
    qual = summary.get("qualitative") or {}
    pins = qual.get("pins") or {}
    if pins.get("n"):
        macros["nPinMessages"] = _fmt(pins["n"])
        macros["pctPinBreakage"] = _pct(int(pins.get("breakage", 0)), int(pins["n"]))
        macros["nPinTemporary"] = _fmt(pins.get("temporary", 0))
    issues = qual.get("issues") or {}
    if issues.get("n"):
        macros["nIssuesCoded"] = _fmt(issues["n"])
        for key, macro in (
            ("names_rename", "pctIssueRename"),
            ("import_error", "pctIssueImport"),
            ("proposes_pin", "pctIssuePin"),
            ("fresh_install", "pctIssueInstall"),
        ):
            macros[macro] = _pct(int(issues.get(key, 0)), int(issues["n"]))

    header = (
        "% Generated by `mcp-migration stats`. Do not edit.\n"
        f"% Snapshot: {summary['snapshot_date']}\n"
        "% Macros not defined here fall back to numbers-defaults.tex and render as [??].\n"
    )
    body = "".join(f"\\def\\{name}{{{value}}}\n" for name, value in sorted(macros.items()))
    return header + body


def gap_sensitivity(paths: Any, gaps: Sequence[int] = SENSITIVITY_GAPS) -> list[dict[str, Any]]:
    """Episode counts under different clustering gaps.

    Episode boundaries are a free parameter, so the paper reports how the
    dataset changes with it rather than asserting one value is correct.
    """
    from mcp_migration.mine.episodes import build_episodes
    from mcp_migration.storage import read_json, read_jsonl

    rows: list[dict[str, Any]] = []
    for gap in gaps:
        episodes = []
        for commits_file in sorted(paths.commits_dir.glob("*.jsonl")):
            head_file = paths.heads_dir / f"{commits_file.stem}.json"
            head = read_json(head_file) if head_file.exists() else None
            episodes.extend(build_episodes(read_jsonl(commits_file), head, gap))
        durations = [e.duration_days for e in episodes]
        rows.append(
            {
                "max_gap_days": gap,
                "n_episodes": len(episodes),
                "n_migration": sum(e.kind == "migration" for e in episodes),
                "median_duration_days": statistics.median(durations) if durations else 0,
                "max_duration_days": max(durations, default=0),
                "max_commits": max((len(e.shas) for e in episodes), default=0),
            }
        )
    return rows


def load_all(paths: Any) -> dict[str, Any]:
    """Read every pipeline output the summary needs; missing files count as zero."""
    from mcp_migration.collect.filtering import Criteria, exclusion_reason
    from mcp_migration.report.labels import read_sheet as read_sheet_rows
    from mcp_migration.report.lag import corpus_seeds, load_lag
    from mcp_migration.storage import read_json, read_jsonl, read_lines

    metadata = list(read_jsonl(paths.repo_meta)) if paths.repo_meta.exists() else []
    exclusions: Counter[str] = Counter()
    for meta in metadata:
        if (reason := exclusion_reason(meta, Criteria())) is not None:
            exclusions[reason] += 1
    return {
        "episodes": list(read_jsonl(paths.episodes)) if paths.episodes.exists() else [],
        "heads": [read_json(p) for p in sorted(paths.heads_dir.glob("*.json"))],
        # Count the per-query files, not the merged one: the merge runs only
        # after every query completes, so a partial search would report zero.
        "n_search_hits": sum(
            sum(1 for _ in read_jsonl(f)) for f in sorted(paths.search_dir.glob("*.jsonl"))
        ),
        "n_seed_repos": len(read_lines(paths.repo_list)) if paths.repo_list.exists() else 0,
        "n_signal_commits": _count_commits(paths.commits_dir.glob("*.jsonl")),
        "n_resolved_repos": len(metadata),
        "repo_sizes": [int(m.get("disk_usage_kb") or 0) for m in metadata],
        "metadata": metadata,
        "repo_queries": _repo_queries(paths),
        "n_clone_candidates": (
            len(read_lines(paths.clone_list)) if paths.clone_list.exists() else 0
        ),
        "exclusions": dict(exclusions),
        "gap_sensitivity": gap_sensitivity(paths),
        "labels": (read_json(paths.label_report) if paths.label_report.exists() else {}),
        "qualitative": (read_json(paths.qualitative) if paths.qualitative.exists() else {}),
        "lag": load_lag(paths, seeds := corpus_seeds(paths)),
        "lag_all": load_lag(paths),
        # Counted from what ran, not from the config: a query that never
        # completed left no file and contributed nothing.
        "n_search_queries": len(list(paths.search_dir.glob("*.jsonl"))),
        "n_sample_size": (
            sum(1 for _ in read_sheet_rows(paths.labeling_sheet))
            if paths.labeling_sheet.exists()
            else 0
        ),
        "harvest_outcomes": (
            list(read_jsonl(paths.harvest_outcomes)) if paths.harvest_outcomes.exists() else []
        ),
        "lag_repos": sum(
            1 for f in paths.commits_dir.glob("*.jsonl") if f.stem.replace("__", "/", 1) in seeds
        ),
    }


def _count_commits(files: Iterable[Path]) -> int:
    return sum(
        sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip()) for f in files
    )


def _repo_queries(paths: Any) -> dict[str, set[str]]:
    """Which search queries each repository was found by."""
    from mcp_migration.storage import read_jsonl

    found: dict[str, set[str]] = {}
    for path in sorted(paths.search_dir.glob("*.jsonl")):
        for hit in read_jsonl(path):
            repo = hit.get("repo")
            if repo:
                found.setdefault(str(repo), set()).add(path.stem)
    return found
