"""Commit-history mining with PyDriller."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from git import Blob, Repo
from pydriller import Git, Repository

from mcp_migration.mine.classify import author_kind, is_migration_candidate, static_era
from mcp_migration.mine.signals import (
    PIN_BELOW_2_KEY,
    PREFILTER_PATHSPECS,
    PREFILTER_REGEX,
    SKIP_DIRS,
    count_signals,
    is_relevant_path,
)
from mcp_migration.models import CommitRecord, HeadRecord
from mcp_migration.repos import slug_from_dir_name
from mcp_migration.storage import first_line, write_jsonl

log = logging.getLogger(__name__)


@dataclass
class MineResult:
    repo_dir: Path
    commits: list[CommitRecord]
    head: HeadRecord | None
    error: str | None = None


def _commit_record(repo: str, commit: Any) -> CommitRecord | None:
    added: Counter[str] = Counter()
    removed: Counter[str] = Counter()
    files: list[str] = []
    for mod in commit.modified_files:
        path = mod.new_path or mod.old_path
        if not is_relevant_path(path):
            continue
        diff = mod.diff_parsed
        file_added = count_signals(text for _, text in diff["added"])
        file_removed = count_signals(text for _, text in diff["deleted"])
        if file_added or file_removed:
            files.append(path)
            added.update(file_added)
            removed.update(file_removed)
    if not (added or removed):
        return None
    return CommitRecord(
        repo=repo,
        sha=commit.hash,
        parent_sha=commit.parents[0] if commit.parents else "",
        date=commit.committer_date.astimezone(UTC).isoformat(),
        author_kind=author_kind(commit.author.name, commit.author.email, commit.msg),
        is_merge=commit.merge,
        message=first_line(commit.msg),
        signal_files=files,
        lines_added=commit.insertions,
        lines_deleted=commit.deletions,
        added=dict(added),
        removed=dict(removed),
        migration_candidate=is_migration_candidate(added, removed),
        pin_below_2_added=added.get(PIN_BELOW_2_KEY, 0) > 0,
    )


def _iter_head_lines(repo_dir: Path) -> Iterator[str]:
    """Yield lines of every relevant file in the HEAD tree.

    Read from git's object database rather than the working tree. Clones are
    made with --no-checkout to save roughly 70% of disk, so there is no working
    tree to walk; this also confines the scan to tracked files, where walking
    the filesystem would pick up untracked build output and local scratch files.
    """
    for item in Repo(str(repo_dir)).head.commit.tree.traverse():
        if not isinstance(item, Blob):
            continue
        path = str(item.path)
        if not is_relevant_path(path) or SKIP_DIRS.intersection(PurePosixPath(path).parts):
            continue
        try:
            data: bytes = item.data_stream.read()
        except Exception as exc:  # unreadable or corrupt object
            log.debug("skipping %s: %s", path, exc)
            continue
        yield from data.decode("utf-8", errors="ignore").splitlines()


def scan_head(repo_dir: Path) -> HeadRecord:
    counts = count_signals(_iter_head_lines(repo_dir))
    return HeadRecord(
        repo=slug_from_dir_name(repo_dir.name),
        head_sha=Git(str(repo_dir)).get_head().hash,
        signals=dict(counts),
        static_era=static_era(counts),
    )


TIMEOUT_PREFIX = "timeout: "


class MiningTimeout(Exception):
    """Mining a repository exceeded its time budget."""


def candidate_shas(
    repo_dir: Path, since: datetime | None = None, timeout_s: float | None = None
) -> set[str]:
    """Commits whose diff could contain a signal, as git itself finds them.

    The same history pydriller walks -- HEAD, rev-list's own --since -- narrowed
    with `git log -G` to commits that add or remove a line matching
    PREFILTER_REGEX in a file matching PREFILTER_PATHSPECS. Both are supersets
    of what the miner counts, so this can only drop commits that would have
    produced no record: merges get no diff from pydriller either, and a root
    commit is diffed against the empty tree by both.

    With a pathspec, git simplifies history and skips a side branch whose
    changes a merge later discarded, while pydriller walks every commit. That
    dropped real signal commits when compared against the full walk (3 of 143 in
    evalstate/fast-agent), hence --full-history. --no-renames is a precaution
    rather than a fix for anything observed: rename detection can only shrink
    the lines a diff shows, so turning it off can only let more through.
    """
    import subprocess

    cmd = [
        "git",
        "-C",
        str(repo_dir),
        "log",
        "--full-history",
        "--no-renames",
        "--format=%H",
        "-i",
        f"-G{PREFILTER_REGEX}",
    ]
    if since is not None:
        cmd.append(f"--since={since.isoformat()}")
    cmd += ["HEAD", "--", *PREFILTER_PATHSPECS]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    return set(result.stdout.split())


def mine_repo(
    repo_dir: Path,
    since: datetime | None = None,
    budget_s: float | None = None,
    prefilter: bool = False,
) -> MineResult:
    """Mine one local clone. The directory name must be ``owner__repo``.

    ``budget_s`` bounds the wall time spent on one repository. Cost is roughly a
    second per commit in the window -- pydriller materialises a full diff for
    each -- so it tracks commit count, which is not knowable before cloning and
    is only loosely related to repository size. A budget is therefore a more
    honest admission criterion than a size cap: it states what we were prepared
    to spend rather than guessing which repositories would be expensive.

    The deadline is checked between commits, so a single pathological commit can
    overrun it. Abandoning is all-or-nothing: a partial walk would look like a
    repository with few signal commits, which is worse than no record at all.

    ``prefilter`` has git choose the commits first (see ``candidate_shas``), so
    only those are diffed. The records are the same either way. The budget
    covers the git pass too: it runs as a subprocess the per-commit check cannot
    interrupt, so it is given what remains of the budget as its own timeout.
    """
    import subprocess

    repo = slug_from_dir_name(repo_dir.name)
    deadline = time.monotonic() + budget_s if budget_s else None
    try:
        only = None
        if prefilter:
            try:
                only = candidate_shas(repo_dir, since, budget_s)
            except subprocess.TimeoutExpired:
                raise MiningTimeout(f"exceeded {budget_s:.0f}s in the prefilter") from None
        commits: list[CommitRecord] = []
        walk = Repository(str(repo_dir), since=since, order="topo-order", only_commits=only)
        for commit in walk.traverse_commits():
            if deadline is not None and time.monotonic() > deadline:
                raise MiningTimeout(f"exceeded {budget_s:.0f}s after {len(commits)} signals")
            if (record := _commit_record(repo, commit)) is not None:
                commits.append(record)
        return MineResult(repo_dir, commits, scan_head(repo_dir))
    except MiningTimeout as exc:
        return MineResult(repo_dir, [], None, error=f"{TIMEOUT_PREFIX}{exc}")
    except Exception as exc:  # one broken repo must not stop a large run
        return MineResult(repo_dir, [], None, error=f"{type(exc).__name__}: {exc}")


def mine_many(
    repo_dirs: Iterable[Path], since: datetime | None, workers: int
) -> Iterator[MineResult]:
    """Mine repositories in parallel processes, yielding results as they finish."""
    dirs = list(repo_dirs)
    if workers <= 1:
        for repo_dir in dirs:
            yield mine_repo(repo_dir, since)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(mine_repo, d, since) for d in dirs]
        for future in as_completed(futures):
            yield future.result()


def backfill_parents(commits_file: Path, repo_dir: Path) -> int:
    """Add ``parent_sha`` to records mined before the field existed.

    A compare link needs the commit before an episode starts. That used to be
    resolved from the clone at export time, which stops working the moment a
    harvest deletes the clone -- so the parent has to be captured into the
    records while the clone is still here. Returns the number of records filled.

    One `git rev-list` per repository rather than one per commit: the corpus has
    192k signal commits and 30k repositories, and the difference is hours.
    """
    import json
    import subprocess

    raw = commits_file.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in raw if line.strip()]
    # Key presence, not truthiness: a root commit has no parent, and "" is the
    # right answer for it. Testing the value would mark those unfilled forever
    # and redo the whole corpus on every run.
    todo = [r["sha"] for r in records if "parent_sha" not in r]
    if not todo:
        return 0
    if not (repo_dir / ".git").exists():
        return 0

    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-list", "--no-walk", "--parents", *todo],
        capture_output=True,
        text=True,
    )
    parents: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts:
            # "<sha> [<parent>...]"; a root commit lists none.
            parents[parts[0]] = parts[1] if len(parts) > 1 else ""

    filled = 0
    for record in records:
        if "parent_sha" not in record and record["sha"] in parents:
            record["parent_sha"] = parents[record["sha"]]
            filled += 1
    if filled:
        write_jsonl(commits_file, iter(records))
    return filled
