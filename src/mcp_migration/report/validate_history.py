"""Check reconstructed historical states against the real Git trees.

Episode states and RQ2's cohort eligibility rebuild a repository's signal
counts at a past moment by subtracting later changes from HEAD. Non-negative
counts are only a sanity check, so this compares the reconstruction with the
counts actually present in the tree of sampled commits, read from the object
database without a checkout.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp_migration.mine.classify import static_era

# The signals that decide a repository's era and RQ2's eligibility.
STATE_KEYS = (
    "py_v1_fastmcp_import",
    "py_v2_mcpserver_import",
    "py_pin_mcp_v2",
    "py_pin_mcp_below_2",
    "ts_v1_sdk_pkg",
    "ts_v2_split_pkg",
    "modern_server_discover",
    "modern_subscriptions_listen",
    "modern_input_required",
    "py_standalone_fastmcp",
)


def tree_counts(repo_dir: Path, sha: str) -> dict[str, int]:
    """Signal counts over the relevant files of one commit's tree."""
    from pathlib import PurePosixPath

    from git import Blob, Repo

    from mcp_migration.mine.signals import SKIP_DIRS, count_signals, is_relevant_path

    lines: list[str] = []
    for item in Repo(str(repo_dir)).commit(sha).tree.traverse():
        if not isinstance(item, Blob):
            continue
        path = str(item.path)
        if not is_relevant_path(path) or SKIP_DIRS.intersection(PurePosixPath(path).parts):
            continue
        try:
            lines.extend(item.data_stream.read().decode("utf-8", errors="ignore").splitlines())
        except Exception:  # unreadable object: skipped, as the miner skips it
            continue
    counts = count_signals(lines)
    return {k: n for k, n in counts.items() if n > 0}


def compare_repo(
    repo_dir: Path,
    commits: Sequence[Mapping[str, Any]],
    head_signals: Mapping[str, int],
    per_repo: int = 3,
    seed: int = 5,
) -> list[dict[str, Any]]:
    """Reconstructed against actual state just after sampled signal commits."""
    from mcp_migration.mine.episodes import Rewinder

    ordered = sorted((c for c in commits if not c.get("is_merge")), key=lambda c: str(c["date"]))
    if not ordered:
        return []
    rewinder = Rewinder(ordered, head_signals)
    rows = []
    for commit in random.Random(seed).sample(ordered, min(per_repo, len(ordered))):
        rebuilt = rewinder.counts(str(commit["date"]), inclusive=False)
        actual = tree_counts(repo_dir, str(commit["sha"]))
        keys_equal = all(rebuilt.get(k, 0) == actual.get(k, 0) for k in STATE_KEYS)
        rows.append(
            {
                "sha": commit["sha"],
                "counts_equal": keys_equal,
                "era_equal": static_era(rebuilt) == static_era(actual),
                "rebuilt_era": str(static_era(rebuilt)),
                "actual_era": str(static_era(actual)),
            }
        )
    return rows
