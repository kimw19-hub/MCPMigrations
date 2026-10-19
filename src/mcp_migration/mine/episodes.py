"""Group signal commits into migration episodes."""

from __future__ import annotations

from collections import Counter
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
    V1_SDK_KEYS,
    V2_ADDED_KEYS,
    V2_EVIDENCE_KEYS,
)
from mcp_migration.models import Episode

DEFAULT_MAX_GAP_DAYS = 30
# Touches a version marker without any sign of a migration decision. Kept out
# of episodes.jsonl but written beside it, so what the rules discard can be
# audited and the remaining episode ids do not shift.
INCIDENTAL = "incidental"


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


def _total(group: list[Mapping[str, Any]], side: str, keys: Iterable[str]) -> int:
    keys = tuple(keys)
    return sum(c[side].get(k, 0) for c in group for k in keys)


def _revision_counts(group: list[Mapping[str, Any]], side: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for c in group:
        for key, n in c[side].items():
            if key.startswith(REVISION_PREFIX):
                counts[key.removeprefix(REVISION_PREFIX)] += n
    return counts


def _leaves_v1_for_standalone_fastmcp(group: list[Mapping[str, Any]]) -> bool:
    """More v1 SDK lines went than came, and standalone FastMCP arrived.

    Removing a v1 line alone is not a decision: in the coded sample 93 of 105
    such episodes were minor bumps, refactors, deleted examples or fork
    history. A v1 removal with nothing taking over is a cleanup. A move to
    standalone FastMCP is what made most of the genuine ones.
    """
    v1_net = _total(group, "removed", V1_SDK_KEYS) - _total(group, "added", V1_SDK_KEYS)
    return v1_net > 0 and _total(group, "added", ("py_standalone_fastmcp",)) > 0


def _changes_spoken_revision(group: list[Mapping[str, Any]]) -> bool:
    """A revision literal went and a different one gained ground.

    A date that only appears is usually a test fixture, a doc link or new code
    written on an old revision; the coders rejected 95 of 105 such episodes.
    Replacing one revision with another is the project changing what it speaks.
    """
    added, removed = _revision_counts(group, "added"), _revision_counts(group, "removed")
    return any(new != old and added[new] - removed[new] > 0 for old in removed for new in added)


def _episode_kind(group: list[Mapping[str, Any]]) -> str:
    v2_in = any(_has(c["added"], V2_EVIDENCE_KEYS) for c in group)
    v1_out = any(_has(c["removed"], V1_REMOVED_KEYS) for c in group)
    if v2_in and v1_out:
        return "migration"
    if v2_in:
        return "v2_adoption"  # greenfield v2 or additive; no v1 removal observed
    if any(c["added"].get(PIN_BELOW_2_KEY, 0) > 0 for c in group):
        return "pin_below_2"
    if any(c["added"].get("py_v2_MCPError", 0) > 0 for c in group) and not v1_out:
        # A project's own error class arriving with new code; its revision
        # literals are that code's, not a change in what the project speaks.
        return INCIDENTAL
    if v1_out:
        # No fall-through to revision_change: inside v1 churn (forks, refactors,
        # mass deletions) literals move as noisily as imports do, and 9 of the
        # 12 coded episodes the revision rule would rescue were false.
        return "v1_removal" if _leaves_v1_for_standalone_fastmcp(group) else INCIDENTAL
    if _changes_spoken_revision(group):
        return "revision_change"
    return INCIDENTAL


class Rewinder:
    """Signal state at any moment, rebuilt backwards from HEAD.

    The harvest deleted the checkout, so the state cannot be read: it is HEAD's
    counts minus every change committed after the moment. Both sides count
    matching lines over the same relevant files, so the subtraction is exact,
    except for changes only a merge commit introduces, which carry no diff here.
    Commits are ordered by committer date, as the episodes themselves are.

    Suffix sums make each lookup one subtraction rather than a pass over the
    history; with five clustering gaps and thousands of episodes per large
    repository, the difference is the bulk of `stats`' running time.
    """

    def __init__(self, commits: Iterable[Mapping[str, Any]], head: Mapping[str, int]) -> None:
        ordered = sorted(commits, key=lambda c: str(c["date"]))
        self.dates = [str(c["date"]) for c in ordered]
        self.head = Counter({k: int(v) for k, v in head.items()})
        running: Counter[str] = Counter()
        suffix = [Counter[str]()] * (len(ordered) + 1)
        for k in range(len(ordered) - 1, -1, -1):
            running = running.copy()
            running.update(ordered[k]["added"])
            running.subtract(ordered[k]["removed"])
            suffix[k] = running
        self.suffix = suffix

    def counts(self, moment: str, inclusive: bool) -> dict[str, int]:
        """Signal counts once every commit after ``moment`` (or at it, if inclusive) is undone."""
        from bisect import bisect_left, bisect_right

        cut = bisect_left(self.dates, moment) if inclusive else bisect_right(self.dates, moment)
        counts = self.head.copy()
        counts.subtract(self.suffix[cut])
        return {k: n for k, n in counts.items() if n > 0}

    def era(self, moment: str, inclusive: bool) -> str:
        """Era once every commit after ``moment`` (or at it, if inclusive) is undone."""
        from mcp_migration.mine.classify import static_era

        return str(static_era(self.counts(moment, inclusive)))


def states_around(
    group: list[Mapping[str, Any]], rewinder: Rewinder | None
) -> tuple[str | None, str | None]:
    """The static era just before an episode's first commit and just after its last."""
    if rewinder is None:
        return None, None
    return (
        rewinder.era(str(group[0]["date"]), inclusive=True),
        rewinder.era(str(group[-1]["date"]), inclusive=False),
    )


def _build(
    repo: str,
    index: int,
    group: list[Mapping[str, Any]],
    head_era: str | None,
    states: tuple[str | None, str | None] = (None, None),
) -> Episode:
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
        era_before=states[0],
        era_after=states[1],
    )


def build_episodes(
    commits: Iterable[Mapping[str, Any]],
    head: Mapping[str, Any] | None = None,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> list[Episode]:
    """Cluster migration-related commits of one repository by time gap."""
    # Every signal commit, not only the migration-related ones: rebuilding the
    # state around an episode has to undo all later changes to what HEAD counts.
    every = list(commits)
    related = sorted(
        (c for c in every if not c["is_merge"] and is_migration_related(c)),
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
    head_signals = head.get("signals") if head else None
    rewinder = Rewinder(every, head_signals) if head_signals is not None else None
    return [_build(repo, i, g, head_era, states_around(g, rewinder)) for i, g in enumerate(groups)]
