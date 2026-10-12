"""Everything a coder needs to judge one episode, gathered in one place.

Two layers. The mined records give the commit list, the signals each commit
moved and the files they moved in, and are always available offline. GitHub
adds what mining did not keep -- full commit messages with their trailers, the
diff itself, the pull request a commit arrived in -- which are exactly the
things the codebook sends a coder to look at for `trigger` and
`author_kind_verified`. Those are fetched one commit at a time, on request, and
cached on disk so a commit costs one API call however often it is opened.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from mcp_migration.mine.classify import _AGENT_RX
from mcp_migration.mine.signals import count_signals
from mcp_migration.repos import repo_dir_name
from mcp_migration.storage import read_jsonl

log = logging.getLogger(__name__)

GITHUB = "https://github.com"
API = "https://api.github.com"
# Issues and pull requests opened this long either side of an episode are the
# ones most likely to say why it happened.
TRIGGER_WINDOW = timedelta(days=14)
# A vendored lockfile can make one patch tens of thousands of lines long.
MAX_PATCH_LINES = 600
MAX_PR_BODY = 4000
_TRAILER_RX = re.compile(r"^\s*([A-Za-z][A-Za-z-]*-by|Generated with[^:]*)\s*:?\s*(.*)$", re.I)


def file_diff_url(repo: str, sha: str, path: str) -> str:
    """Link straight to one file's diff on a commit page.

    GitHub anchors each file at ``#diff-`` plus the SHA-256 of its path.
    """
    anchor = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return f"{GITHUB}/{repo}/commit/{sha}#diff-{anchor}"


def range_url(repo: str, shas: list[str], base: str) -> str:
    """The whole episode as one diff: a commit, or a compare from its parent."""
    if len(shas) == 1:
        return f"{GITHUB}/{repo}/commit/{shas[0]}"
    # From the parent, so the first commit's own change is included. A root
    # commit has none, and comparing from it is the closest GitHub offers.
    return f"{GITHUB}/{repo}/compare/{base or shas[0]}...{shas[-1]}"


def _window(start: str, end: str) -> str:
    a = datetime.fromisoformat(start) - TRIGGER_WINDOW
    b = datetime.fromisoformat(end) + TRIGGER_WINDOW
    return f"{a.date().isoformat()}..{b.date().isoformat()}"


def load_commits(commits_dir: Path, repo: str) -> dict[str, dict[str, Any]]:
    path = commits_dir / f"{repo_dir_name(repo)}.jsonl"
    return {c["sha"]: c for c in read_jsonl(path)} if path.exists() else {}


def episode_context(
    episode: Mapping[str, Any], commits: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The episode, its commits in order, and the links a coder follows."""
    repo = str(episode["repo"])
    shas = [str(s) for s in episode["shas"]]
    first = commits.get(shas[0], {})
    window = _window(str(episode["start"]), str(episode["end"]))
    rows = []
    for sha in shas:
        c = commits.get(sha)
        if c is None:
            rows.append({"sha": sha, "missing": True, "url": f"{GITHUB}/{repo}/commit/{sha}"})
            continue
        rows.append(
            {
                "sha": sha,
                "date": c["date"],
                "author_kind": c["author_kind"],
                "message": c["message"],
                "lines_added": c["lines_added"],
                "lines_deleted": c["lines_deleted"],
                "added": c["added"],
                "removed": c["removed"],
                "files": [
                    {"path": p, "url": file_diff_url(repo, sha, p)} for p in c["signal_files"]
                ],
                "url": f"{GITHUB}/{repo}/commit/{sha}",
            }
        )
    return {
        **{k: episode[k] for k in episode if k != "shas"},
        "n_commits": len(shas),
        "commits": rows,
        "links": {
            "repo": f"{GITHUB}/{repo}",
            "range": range_url(repo, shas, str(first.get("parent_sha", ""))),
            "issues": f"{GITHUB}/{repo}/issues?q=is%3Aissue+created%3A{window}",
            "pulls": f"{GITHUB}/{repo}/pulls?q=is%3Apr+created%3A{window}",
            "history": f"{GITHUB}/{repo}/commits/{shas[-1]}",
        },
    }


def search_text(episode: Mapping[str, Any], commits: Mapping[str, Mapping[str, Any]]) -> str:
    """What the app's search box matches against, lower-cased."""
    parts = [str(episode["episode_id"]), str(episode["repo"])]
    for sha in episode["shas"]:
        c = commits.get(sha)
        if c:
            parts += [sha, c["message"], *c["signal_files"], *c["added"], *c["removed"]]
    return "\n".join(parts).lower()


def annotate_patch(patch: str) -> dict[str, Any]:
    """Split a patch into lines, marking the signals each changed line matches.

    Matching is done here with the miner's own patterns rather than ported to
    the browser, so what is highlighted is exactly what the detector counted.
    """
    lines = patch.splitlines()
    out = []
    for line in lines[:MAX_PATCH_LINES]:
        changed = line[:1] in "+-" and not line.startswith(("+++", "---"))
        signals = sorted(count_signals([line[1:]])) if changed else []
        out.append({"t": line, "s": signals})
    return {"lines": out, "truncated": max(0, len(lines) - MAX_PATCH_LINES)}


def trailers(message: str) -> list[dict[str, Any]]:
    """Trailer-like lines (Co-authored-by, Signed-off-by, ...) and whether they name an agent."""
    found = []
    for line in message.splitlines():
        match = _TRAILER_RX.match(line)
        if match:
            found.append({"text": line.strip(), "agent": bool(_AGENT_RX.search(line))})
    return found


class GitHubFetcher:
    """Commit details from the REST API, cached as JSON files."""

    def __init__(
        self,
        cache_dir: Path,
        token: str | None,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "mcp-migration-dataset",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.authenticated = bool(token)

    def _get(self, path: str) -> Any:
        resp = self.session.get(f"{API}{path}", timeout=30)
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            hint = "" if self.authenticated else "; set GITHUB_TOKEN to raise the limit"
            raise LookupError(f"GitHub rate limit reached{hint}")
        if resp.status_code == 404 or resp.status_code == 422:
            raise LookupError("GitHub no longer has this commit (deleted or force-pushed)")
        resp.raise_for_status()
        return resp.json()

    def commit(self, repo: str, sha: str, signal_files: Iterable[str]) -> dict[str, Any]:
        cached = self.cache_dir / repo_dir_name(repo) / f"{sha}.json"
        if cached.exists():
            data: dict[str, Any] = json.loads(cached.read_text(encoding="utf-8"))
            return data
        raw = self._get(f"/repos/{repo}/commits/{sha}")
        pulls = self._get(f"/repos/{repo}/commits/{sha}/pulls")
        wanted = set(signal_files)
        message = str(raw["commit"]["message"])
        files = [
            {
                "path": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "signal": f["filename"] in wanted,
                "url": file_diff_url(repo, sha, f["filename"]),
                **annotate_patch(f.get("patch") or ""),
            }
            for f in raw.get("files", [])
        ]
        # Signal files first: they are why the commit is in the episode.
        files.sort(key=lambda f: not f["signal"])
        author = raw.get("author") or {}
        data = {
            "message": message,
            "trailers": trailers(message),
            "author_login": author.get("login"),
            "author_type": author.get("type"),
            "committer_login": (raw.get("committer") or {}).get("login"),
            "files": files,
            "pulls": [
                {
                    "number": p["number"],
                    "title": p["title"],
                    "url": p["html_url"],
                    "state": "merged" if p.get("merged_at") else p["state"],
                    "user": (p.get("user") or {}).get("login"),
                    "user_type": (p.get("user") or {}).get("type"),
                    "body": (p.get("body") or "")[:MAX_PR_BODY],
                }
                for p in pulls
            ],
        }
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
        return data
