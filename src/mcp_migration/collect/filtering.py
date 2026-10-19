"""Inclusion criteria applied between `seed` and `clone`.

These are the dataset's stated inclusion criteria and belong in the paper's
method section, so each exclusion reason is counted and reported rather than
applied silently.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

# A repository last pushed before the first public revision cannot contain any
# migration we study. This is a correctness guard rather than a volume filter:
# measured on 8,000 resolved repositories it excluded 0.1%, because the MCP
# ecosystem is almost entirely younger than the cutoff.
DEFAULT_PUSHED_AFTER = date(2024, 11, 1)
# Repository sizes are extremely skewed: over the 35,368 resolved repositories
# the median is 1.4 MB but the mean is 62 MB, and the largest single entry
# (chromium/chromium, a search false positive) is 63 GB on its own. A 2 GB cap
# keeps 99.6% of them and drops 140 that hold 576 GB between them.
#
# Storage is not what the cap buys: `harvest` clones, mines and deletes one
# repository at a time, so disk is bounded by what is in flight. What it buys is
# mining time, which tracks history length, and the repositories above it are
# mostly large codebases that matched a revision date rather than the SDK --
# `report.stats.exclusion_profile` computes that comparison for the papers.
#
# Unlike DEFAULT_PUSHED_AFTER above this is a tractability cut, not a
# correctness one: a repository over the cap can perfectly well contain a
# migration. So the cap is a stated criterion, reported by `filter` and
# reversible with --max-disk-kb; individual repositories can be forced back in
# via Criteria.keep. It was 100 MB until the harvest made storage moot.
#
# `report.stats.size_profile` recomputes these figures in the summary, so
# do not restate them there by hand.
DEFAULT_MAX_DISK_KB = 2_000_000  # 2 GB

# The official SDK and specification repositories implement the breaking change
# rather than react to it: their histories contain every signal by construction
# and would dominate any aggregate. They are excluded from the server population
# and studied separately if at all.
UPSTREAM_REPOS = frozenset(
    {
        "modelcontextprotocol/python-sdk",
        "modelcontextprotocol/typescript-sdk",
        "modelcontextprotocol/go-sdk",
        "modelcontextprotocol/csharp-sdk",
        "modelcontextprotocol/java-sdk",
        "modelcontextprotocol/kotlin-sdk",
        "modelcontextprotocol/rust-sdk",
        "modelcontextprotocol/specification",
        "modelcontextprotocol/modelcontextprotocol",
        "modelcontextprotocol/servers",
    }
)


@dataclass(frozen=True)
class Criteria:
    pushed_after: date = DEFAULT_PUSHED_AFTER
    max_disk_kb: int | None = DEFAULT_MAX_DISK_KB
    exclude_archived: bool = True
    exclude_forks: bool = True
    exclude_upstream: bool = True
    keep: frozenset[str] = frozenset()  # always included, whatever the metadata says


def exclusion_reason(meta: Mapping[str, object], criteria: Criteria) -> str | None:
    """Return why this repository is excluded, or None to keep it."""
    if str(meta.get("repo", "")).lower() in criteria.keep:
        return None
    if criteria.exclude_upstream and str(meta.get("repo", "")).lower() in UPSTREAM_REPOS:
        return "upstream_sdk"
    if meta.get("empty"):
        return "empty"
    if criteria.exclude_archived and meta.get("archived"):
        return "archived"
    if criteria.exclude_forks and meta.get("fork"):
        return "fork"

    pushed = meta.get("pushed_at")
    if not pushed:
        return "no_push_date"
    if datetime.fromisoformat(str(pushed)).date() < criteria.pushed_after:
        return "pushed_before_cutoff"

    disk = meta.get("disk_usage_kb")
    if criteria.max_disk_kb is not None and isinstance(disk, int) and disk > criteria.max_disk_kb:
        return "too_large"
    return None


def apply(
    metadata: Iterable[Mapping[str, object]], criteria: Criteria
) -> tuple[list[str], Counter[str]]:
    """Partition repositories into those to clone and a census of exclusions."""
    kept: list[str] = []
    reasons: Counter[str] = Counter()
    for meta in metadata:
        reason = exclusion_reason(meta, criteria)
        if reason is None:
            kept.append(str(meta["repo"]))
        else:
            reasons[reason] += 1
    return sorted(set(kept)), reasons
