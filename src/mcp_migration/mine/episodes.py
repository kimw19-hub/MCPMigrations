"""Group signal commits into migration episodes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from mcp_migration.mine.signals import (
    DEPRECATED_KEYS,
    PIN_BELOW_2_KEY,
    PROTOCOL_ERA_KEYS,
    REPLACEMENT_PAIRS,
    REVISION_PREFIX,
    SDK_GENERATION_KEYS,
    V1_REMOVED_KEYS,
    V2_ADDED_KEYS,
)
from mcp_migration.models import Episode

DEFAULT_MAX_GAP_DAYS = 30


def _has(counts: Mapping[str, int], keys: Iterable[str]) -> bool:
    return any(counts.get(k, 0) > 0 for k in keys)


def _revisions(counts: Mapping[str, int]) -> set[str]:
    return {k.removeprefix(REVISION_PREFIX) for k in counts if k.startswith(REVISION_PREFIX)}


def is_migration_related(record: Mapping[str, Any]) -> bool:
    added, removed = record["added"], record["removed"]
    return (
        _has(added, V2_ADDED_KEYS)
        or _has(removed, V1_REMOVED_KEYS)
        or added.get(PIN_BELOW_2_KEY, 0) > 0
        or bool(_revisions(added) or _revisions(removed))
    )


def _episode_scope(group: list[Mapping[str, Any]]) -> str:
    """What the episode changed: the SDK generation, the protocol, or both.

    Derived rather than coded. The signals needed are already mined, so this
    covers every episode instead of only a sampled few, and it is exact where a
    coder would have been judging from the same diff.
    """
    sides = ("added", "removed")
    touched_sdk = any(_has(c[side], SDK_GENERATION_KEYS) for c in group for side in sides)
    touched_protocol = any(
        _revisions(c["added"]) or _revisions(c["removed"]) or _has(c[side], PROTOCOL_ERA_KEYS)
        for c in group
        for side in sides
    )
    if touched_sdk and touched_protocol:
        return "sdk_and_protocol"
    if touched_sdk:
        return "sdk_major_only"
    return "protocol_only"


def _episode_kind(group: list[Mapping[str, Any]]) -> str:
    v2_in = any(_has(c["added"], V2_ADDED_KEYS) for c in group)
    v1_out = any(_has(c["removed"], V1_REMOVED_KEYS) for c in group)
    if v2_in and v1_out:
        return "migration"
    if v2_in:
        return "v2_adoption"  # greenfield v2 or additive; no v1 removal observed
    if any(c["added"].get(PIN_BELOW_2_KEY, 0) > 0 for c in group):
        return "pin_below_2"
    if v1_out:
        return "v1_removal"
    return "revision_change"


def _build(repo: str, index: int, group: list[Mapping[str, Any]], head_era: str | None) -> Episode:
    start = datetime.fromisoformat(group[0]["date"])
    end = datetime.fromisoformat(group[-1]["date"])
    added_revs: set[str] = set()
    removed_revs: set[str] = set()
    deprecated_removed: set[str] = set()
    all_added: set[str] = set()
    all_removed: set[str] = set()
    for c in group:
        added_revs |= _revisions(c["added"])
        removed_revs |= _revisions(c["removed"])
        deprecated_removed |= {k for k in DEPRECATED_KEYS if c["removed"].get(k, 0) > 0}
        all_added |= {k for k, n in c["added"].items() if n > 0}
        all_removed |= {k for k, n in c["removed"].items() if n > 0}
    replacements = {
        label
        for (gone, arrived), label in REPLACEMENT_PAIRS.items()
        if gone in all_removed and arrived in all_added
    }
    return Episode(
        episode_id=f"{repo}#{index}",
        repo=repo,
        kind=_episode_kind(group),
        start=start.isoformat(),
        end=end.isoformat(),
        duration_days=(end - start).days,
        shas=[c["sha"] for c in group],
        lines_added=sum(c["lines_added"] for c in group),
        lines_deleted=sum(c["lines_deleted"] for c in group),
        author_kinds=sorted({c["author_kind"] for c in group}),
        deprecated_removed=sorted(deprecated_removed),
        revisions_added=sorted(added_revs),
        revisions_removed=sorted(removed_revs),
        replacements=sorted(replacements),
        scope=_episode_scope(group),
        head_static_era=head_era,
    )


def build_episodes(
    commits: Iterable[Mapping[str, Any]],
    head: Mapping[str, Any] | None = None,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> list[Episode]:
    """Cluster migration-related commits of one repository by time gap."""
    related = sorted(
        (c for c in commits if not c["is_merge"] and is_migration_related(c)),
        key=lambda c: c["date"],
    )
    if not related:
        return []
    repo = related[0]["repo"]
    head_era = head.get("static_era") if head else None
    gap = timedelta(days=max_gap_days)

    groups: list[list[Mapping[str, Any]]] = [[related[0]]]
    for commit in related[1:]:
        previous = datetime.fromisoformat(groups[-1][-1]["date"])
        if datetime.fromisoformat(commit["date"]) - previous <= gap:
            groups[-1].append(commit)
        else:
            groups.append([commit])
    return [_build(repo, i, g, head_era) for i, g in enumerate(groups)]
