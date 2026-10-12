"""Clone-mine-delete: the pass that bounds disk by what is in flight."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_migration.config import Paths

SINCE = datetime(2024, 11, 1, tzinfo=UTC)


class _Head:
    static_era = "sdk_v1"

    def to_dict(self) -> dict[str, Any]:
        return {"static_era": self.static_era}


class _Result:
    def __init__(self, repo_dir: Path, error: str | None = None) -> None:
        self.repo_dir, self.error = repo_dir, error
        self.commits: list[Any] = []
        self.head = None if error else _Head()


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Stand in for the network: `clone` just makes a directory that looks cloned."""
    calls: dict[str, list[str]] = {"cloned": [], "mined": []}

    def clone(slug: str, root: Path, timeout: int = 1800) -> bool:
        calls["cloned"].append(slug)
        dest = root / slug.replace("/", "__")
        (dest / ".git").mkdir(parents=True, exist_ok=True)
        (dest / "big.pack").write_bytes(b"x" * 4096)
        return True

    def mine(
        repo_dir: Path,
        since: datetime | None = None,
        budget_s: float | None = None,
        prefilter: bool = False,
    ) -> _Result:
        calls["mined"].append(repo_dir.name)
        return _Result(repo_dir)

    monkeypatch.setattr("mcp_migration.cli.clone_or_update", clone)
    monkeypatch.setattr("mcp_migration.cli.mine_repo", mine)
    return calls


def test_the_clone_is_gone_but_the_mined_output_remains(
    tmp_path: Path, fake_git: dict[str, list[str]]
) -> None:
    from mcp_migration.cli import _harvest_one

    paths = Paths(tmp_path)
    name, error = _harvest_one("owner/repo", paths, SINCE, keep=False)

    assert error is None
    assert not (paths.repos / name).exists(), "the clone is the thing we cannot afford to keep"
    assert (paths.heads_dir / f"{name}.json").exists()


def test_keep_leaves_the_clone_for_re_mining(
    tmp_path: Path, fake_git: dict[str, list[str]]
) -> None:
    """Changing a signal pattern re-downloads everything otherwise."""
    from mcp_migration.cli import _harvest_one

    paths = Paths(tmp_path)
    name, _ = _harvest_one("owner/repo", paths, SINCE, keep=True)
    assert (paths.repos / name / ".git").exists()


def test_a_clone_we_did_not_create_is_never_deleted(
    tmp_path: Path, fake_git: dict[str, list[str]]
) -> None:
    """A --keep corpus from an earlier run is someone else's working set, and
    `harvest` must not quietly consume it."""
    from mcp_migration.cli import _harvest_one

    paths = Paths(tmp_path)
    existing = paths.repos / "owner__repo"
    (existing / ".git").mkdir(parents=True)
    (existing / "precious").write_text("mined by hand")

    _harvest_one("owner/repo", paths, SINCE, keep=False)
    assert (existing / "precious").exists()


def test_a_failed_mine_still_removes_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_git: dict[str, list[str]]
) -> None:
    """Otherwise one bad repository per thousand fills the disk anyway."""
    from mcp_migration.cli import _harvest_one

    monkeypatch.setattr(
        "mcp_migration.cli.mine_repo",
        lambda repo_dir, since=None, budget_s=None, prefilter=False: _Result(
            repo_dir, error="boom"
        ),
    )
    paths = Paths(tmp_path)
    name, error = _harvest_one("owner/repo", paths, SINCE, keep=False)
    assert error == "boom"
    assert not (paths.repos / name).exists()


def test_harvest_skips_what_is_already_mined(tmp_path: Path) -> None:
    """A run this long has to be resumable after an interrupt."""
    from mcp_migration.cli import harvest_todo

    paths = Paths(tmp_path)
    paths.repo_list.parent.mkdir(parents=True, exist_ok=True)
    paths.repo_list.write_text("a/one\na/two\n", encoding="utf-8")
    paths.heads_dir.mkdir(parents=True, exist_ok=True)
    (paths.heads_dir / "a__one.json").write_text("{}", encoding="utf-8")

    args = argparse.Namespace(since=SINCE.date(), limit=None, workers=1, force=False, keep=False)
    todo, source = harvest_todo(args, paths)
    assert todo == ["a/two"]
    assert source == paths.repo_list

    args.force = True
    assert harvest_todo(args, paths)[0] == ["a/one", "a/two"]


def _git(repo: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_backfill_fills_parents_and_settles_on_root_commits(tmp_path: Path) -> None:
    """A root commit has no parent, and "" is the right answer for it.

    Testing the value rather than the key marks those unfilled forever, so every
    re-run redoes the whole corpus -- 30,000 repositories of git for nothing.
    """
    import json
    import subprocess

    from mcp_migration.mine.history import backfill_parents

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "t")
    shas = []
    for n in range(2):
        (repo / f"f{n}.py").write_text(f"x = {n}")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"c{n}")
        shas.append(_git(repo, "rev-parse", "HEAD"))

    commits = tmp_path / "c.jsonl"
    commits.write_text("".join(json.dumps({"sha": s}) + "\n" for s in shas), encoding="utf-8")

    assert backfill_parents(commits, repo) == 2
    records = [json.loads(line) for line in commits.read_text().splitlines() if line.strip()]
    assert records[0]["parent_sha"] == ""  # the root commit
    assert records[1]["parent_sha"] == shas[0]
    assert backfill_parents(commits, repo) == 0, "a second run must have nothing to do"


def test_backfill_is_a_no_op_without_the_clone(tmp_path: Path) -> None:
    """After a harvest the clone is gone; that must not corrupt the records."""
    import json

    from mcp_migration.mine.history import backfill_parents

    commits = tmp_path / "c.jsonl"
    commits.write_text(json.dumps({"sha": "a" * 40}) + "\n", encoding="utf-8")
    assert backfill_parents(commits, tmp_path / "absent") == 0
    assert json.loads(commits.read_text()) == {"sha": "a" * 40}


def test_a_repository_over_its_budget_is_recorded_as_a_timeout(tmp_path: Path) -> None:
    """The admission criterion is what we were willing to spend, not a guess at
    which repositories would be expensive."""
    import subprocess

    from mcp_migration.mine.history import TIMEOUT_PREFIX, mine_repo

    repo = tmp_path / "owner__repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for cmd in (("user.email", "t@e.st"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(repo), "config", *cmd], check=True)
    for n in range(3):
        (repo / f"f{n}.py").write_text(f"from mcp.server.fastmcp import FastMCP  # {n}")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"c{n}"], check=True)

    # A budget already spent: the very first check trips it.
    result = mine_repo(repo, budget_s=-1.0)
    assert result.error is not None
    assert result.error.startswith(TIMEOUT_PREFIX)
    assert result.head is None, "a partial walk would look like a repo with few signals"

    # And with a real budget it mines normally.
    assert mine_repo(repo, budget_s=600).error is None


def test_timeouts_are_bucketed_apart_from_unavailable_repositories() -> None:
    """The paper reports these separately: one is our budget, one is attrition."""
    from mcp_migration.cli import _outcome_reason
    from mcp_migration.mine.history import TIMEOUT_PREFIX

    assert _outcome_reason(f"{TIMEOUT_PREFIX}exceeded 900s after 12 signals") == "timeout"
    assert _outcome_reason("clone failed") == "unavailable"
    assert _outcome_reason("InvalidGitRepositoryError: broken") == "mining_error"


def test_each_outcome_is_on_disk_as_soon_as_it_is_known(tmp_path: Path) -> None:
    """A run that dies part way must not take the verdicts it reached with it."""
    from mcp_migration.cli import OutcomeLedger
    from mcp_migration.storage import read_jsonl

    path = tmp_path / "harvest_outcomes.jsonl"
    OutcomeLedger(path).record("a__gone", "clone failed")
    # No end-of-run step: the ledger is simply abandoned, as an interrupt would.
    assert [o["reason"] for o in read_jsonl(path)] == ["unavailable"]


def test_a_resumed_run_corrects_rather_than_repeats(tmp_path: Path) -> None:
    from mcp_migration.cli import OutcomeLedger
    from mcp_migration.mine.history import TIMEOUT_PREFIX
    from mcp_migration.storage import read_jsonl

    path = tmp_path / "harvest_outcomes.jsonl"
    first = OutcomeLedger(path)
    first.record("a__slow", "InvalidGitRepositoryError: broken")
    first.record("a__flaky", "clone failed")

    second = OutcomeLedger(path)
    second.record("a__slow", f"{TIMEOUT_PREFIX}exceeded 900s after 3 signals")
    second.record("a__flaky", None)  # mined this time: it was never dropped

    assert {o["repo"]: o["reason"] for o in read_jsonl(path)} == {"a__slow": "timeout"}
    assert len(second) == 1
