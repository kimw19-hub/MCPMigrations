"""Compact exports that back the dataset's web explorer.

Two payloads, split by how they are consumed. The summary holds every
aggregate the charts draw and is small enough to inline into the page, so the
charts render even if nothing else loads. The episode list is the browsable
table and is an order of magnitude larger, so it is fetched separately and the
page degrades to charts-only if it is missing.

Keys are short because the episode list repeats them 28,000 times; the mapping
is documented here rather than in the payload.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from mcp_migration.mine.classify import AuthorKind, StaticEra
from mcp_migration.mine.signals import PROTOCOL_REVISIONS
from mcp_migration.report.stats import EPISODE_KINDS

# episode_id, kind, scope, end state, start date, commits, duration,
# author kinds, revisions added, deprecated removed
EPISODE_KEYS = ("i", "k", "s", "e", "d", "n", "u", "a", "r", "p")


def _month(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%Y-%m")


def summary(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every aggregate the explorer charts, computed once here rather than in
    the browser over 28,000 records."""
    kinds = Counter(str(e["kind"]) for e in episodes)
    eras = Counter(str(e.get("head_static_era") or "") for e in episodes)
    scopes = Counter(str(e.get("scope") or "") for e in episodes)
    deprecated = Counter(k for e in episodes for k in e.get("deprecated_removed", []))
    replacements = Counter(r for e in episodes for r in e.get("replacements", []))

    per_month: dict[str, Counter[str]] = defaultdict(Counter)
    authors: dict[str, Counter[str]] = defaultdict(Counter)
    first_seen: dict[str, dict[str, str]] = {r: {} for r in PROTOCOL_REVISIONS}
    for episode in sorted(episodes, key=lambda e: str(e["start"])):
        month = _month(str(episode["start"]))
        per_month[month][str(episode["kind"])] += 1
        kinds_seen = set(episode.get("author_kinds", []))
        authors[month]["total"] += 1
        if AuthorKind.AI_AGENT_ASSISTED in kinds_seen:
            authors[month]["agent"] += 1
        if AuthorKind.BOT in kinds_seen:
            authors[month]["bot"] += 1
        for revision in episode.get("revisions_added", []):
            bucket = first_seen.get(str(revision))
            if bucket is not None and str(episode["repo"]) not in bucket:
                bucket[str(episode["repo"])] = month

    months = sorted(per_month)
    cumulative: dict[str, list[int]] = {}
    for revision in PROTOCOL_REVISIONS:
        counts = Counter(first_seen[revision].values())
        running, series = 0, []
        for month in months:
            running += counts.get(month, 0)
            series.append(running)
        cumulative[revision] = series

    cross = Counter((str(e["kind"]), str(e.get("head_static_era") or "")) for e in episodes)
    return {
        "generated": datetime.now().date().isoformat(),
        "totals": {
            "episodes": len(episodes),
            "repos": len({str(e["repo"]) for e in episodes}),
        },
        "kinds": {k: kinds.get(k, 0) for k in EPISODE_KINDS},
        "eras": {e.value: eras.get(e.value, 0) for e in StaticEra},
        "scopes": dict(sorted(scopes.items())),
        "deprecated": dict(sorted(deprecated.items())),
        "replacements": dict(sorted(replacements.items())),
        "months": months,
        "byKind": {k: [per_month[m].get(k, 0) for m in months] for k in EPISODE_KINDS},
        "authors": {
            "agent": [authors[m]["agent"] for m in months],
            "bot": [authors[m]["bot"] for m in months],
            "total": [authors[m]["total"] for m in months],
        },
        "revisions": cumulative,
        "signals": signal_vocabulary(),
        "heatmap": {
            "kinds": list(EPISODE_KINDS),
            "eras": [e.value for e in StaticEra],
            "cells": [[cross.get((k, e.value), 0) for e in StaticEra] for k in EPISODE_KINDS],
        },
    }


SHA_PREFIX = 10  # GitHub resolves abbreviated hashes; 40 chars each is 2 MB


def signal_vocabulary() -> list[str]:
    """Every signal name an episode can carry, in a stable order.

    The payload stores indices into this list. Thirty names repeated across
    28,000 episodes would otherwise cost more than the rest of the record.
    """
    from mcp_migration.mine.signals import REVISION_PREFIX, SIGNAL_PATTERNS

    return list(SIGNAL_PATTERNS) + [REVISION_PREFIX + r for r in PROTOCOL_REVISIONS]


def _signal_diffs(
    episodes: Sequence[Mapping[str, Any]], commits_dir: Any, vocabulary: Sequence[str]
) -> dict[str, tuple[list[int], list[int]]]:
    """Signals each episode added and removed, read from the commit records.

    The episode record keeps only revisions and deprecated features, so the
    full picture lives in the per-repository commit files.
    """
    index = {name: i for i, name in enumerate(vocabulary)}
    by_repo: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for episode in episodes:
        by_repo[str(episode["repo"])].append(episode)

    out: dict[str, tuple[list[int], list[int]]] = {}
    for repo, group in by_repo.items():
        path = commits_dir / f"{repo.replace('/', '__')}.jsonl"
        if not path.exists():
            continue
        commits = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                commits[record["sha"]] = record
        for episode in group:
            added: set[int] = set()
            removed: set[int] = set()
            for sha in episode.get("shas", []):
                commit = commits.get(sha)
                if commit is None:
                    continue
                added |= {index[k] for k in commit.get("added", {}) if k in index}
                removed |= {index[k] for k in commit.get("removed", {}) if k in index}
            out[str(episode["episode_id"])] = (sorted(added), sorted(removed))
    return out


def _compare_bases(
    episodes: Sequence[Mapping[str, Any]], commits_dir: Any, repos_dir: Any = None
) -> dict[str, str]:
    """The parent of each multi-commit episode's first commit.

    A compare link has to start one commit *before* the episode, or the first
    commit's own changes are missing from the diff. Writing ``sha^`` into the
    URL would do that, except where the episode opens a repository's history: a
    root commit has no parent and GitHub answers 404. Resolved here so the page
    never has to guess, and episodes that genuinely start at a root commit
    simply get no base.

    Read from the mined commit records, which carry the first parent. This used
    to shell out to `git rev-parse` against the clone, which cannot work once a
    harvest deletes the clone after mining it. `repos_dir` remains as a fallback
    for records mined before ``parent_sha`` existed.
    """
    wanted: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for episode in episodes:
        if len(episode.get("shas", [])) > 1:
            wanted[str(episode["repo"])].append(episode)

    bases: dict[str, str] = {}
    missing: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for repo, group in wanted.items():
        path = commits_dir / f"{repo.replace('/', '__')}.jsonl"
        parents: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if record.get("parent_sha"):
                        parents[record["sha"]] = str(record["parent_sha"])
        for episode in group:
            parent = parents.get(str(episode["shas"][0]), "")
            if len(parent) == 40:
                bases[str(episode["episode_id"])] = parent[:SHA_PREFIX]
            else:
                missing[repo].append(episode)

    if repos_dir is not None and missing:
        bases.update(_compare_bases_from_clones(missing, repos_dir))
    return bases


def _compare_bases_from_clones(
    by_repo: Mapping[str, Sequence[Mapping[str, Any]]], repos_dir: Any
) -> dict[str, str]:
    """Resolve bases from clones, for records mined before `parent_sha`."""
    import subprocess

    bases: dict[str, str] = {}
    for repo, group in by_repo.items():
        path = repos_dir / repo.replace("/", "__")
        if not (path / ".git").exists():
            continue
        # One call per repository rather than one per episode.
        args = [f"{e['shas'][0]}^" for e in group]
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--no-flags", *args],
            capture_output=True,
            text=True,
        )
        lines = result.stdout.split()
        if len(lines) == len(group):
            for episode, parent in zip(group, lines, strict=True):
                if len(parent) == 40:
                    bases[str(episode["episode_id"])] = parent[:SHA_PREFIX]
            continue
        # One unresolvable revision fails the whole batch, so fall back to
        # asking per episode. Otherwise a single root commit would cost every
        # other episode in the same repository its base.
        for episode in group:
            single = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--no-flags", f"{episode['shas'][0]}^"],
                capture_output=True,
                text=True,
            )
            parent = single.stdout.strip()
            if single.returncode == 0 and len(parent) == 40:
                bases[str(episode["episode_id"])] = parent[:SHA_PREFIX]
    return bases


def episode_rows(
    episodes: Sequence[Mapping[str, Any]],
    commits_dir: Any = None,
    repos_dir: Any = None,
) -> list[dict[str, Any]]:
    """The browsable table: one short record per episode.

    Carries the abbreviated first and last hashes so the page can link to the
    actual change -- a single commit, or a compare across the episode's range.
    """
    vocabulary = signal_vocabulary()
    diffs = _signal_diffs(episodes, commits_dir, vocabulary) if commits_dir is not None else {}
    bases = _compare_bases(episodes, commits_dir, repos_dir) if commits_dir else {}
    rows = []
    for e in episodes:
        shas = list(e.get("shas", []))
        added, removed = diffs.get(str(e["episode_id"]), ([], []))
        row = {
            "i": str(e["episode_id"]),
            "k": str(e["kind"]),
            "s": str(e.get("scope") or ""),
            "e": str(e.get("head_static_era") or ""),
            "d": str(e["start"])[:10],
            "n": len(shas),
            "u": int(e.get("duration_days", 0)),
            "a": list(e.get("author_kinds", [])),
            "r": list(e.get("revisions_added", [])),
            "p": list(e.get("deprecated_removed", [])),
            "f": shas[0][:SHA_PREFIX] if shas else "",
            "add": added,
            "del": removed,
        }
        # Only multi-commit episodes need a range; for one commit f is enough.
        if len(shas) > 1:
            row["l"] = shas[-1][:SHA_PREFIX]
            base = bases.get(str(e["episode_id"]))
            if base:
                row["b"] = base
        rows.append(row)
    return rows


def qualitative_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """The coded text, as the explorer's evidence tables read it.

    Passed straight through from ``qualitative.json`` apart from the code
    vocabulary, which the page needs in a stable order to build its filters.
    """
    from mcp_migration.report.qualitative import ISSUE_THEMES, PIN_CODES

    evidence = report.get("evidence") or {}
    return {
        "codes": {"pins": list(PIN_CODES), "issues": list(ISSUE_THEMES)},
        "counts": {"pins": report.get("pins") or {}, "issues": report.get("issues") or {}},
        "pins": list(evidence.get("pins") or []),
        "issues": list(evidence.get("issues") or []),
    }


def dumps(payload: Any) -> str:
    """Compact JSON, ASCII-escaped.

    The episode list repeats its keys 28,000 times. Escaping is not only for
    size: the summary is inlined into a page that cannot declare a charset, and
    developer-written text is full of non-ASCII.
    """
    return json.dumps(payload, separators=(",", ":"))


SUMMARY_OPEN = '<script id="summary-data" type="application/json">'
SUMMARY_CLOSE = "</script>"


def inline_summary(page: str, payload: Mapping[str, Any]) -> str:
    """Write the summary into the page's data block.

    Inlined rather than fetched so the charts render from the file alone. The
    episode list stays external because it is a thousand times larger and only
    the table needs it.
    """
    start = page.index(SUMMARY_OPEN) + len(SUMMARY_OPEN)
    end = page.index(SUMMARY_CLOSE, start)
    # A literal </script> inside the JSON would close the block early.
    body = dumps(payload).replace("</", "<\\/")
    return page[:start] + body + page[end:]
