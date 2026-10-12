"""Cloning, and what it does when the network misbehaves."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mcp_migration.collect.clone import clone_or_update


def _run(outcomes: list[BaseException | None], seen: list[list[str]]):
    """A fake `subprocess.run` that replays a scripted sequence of outcomes."""

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return fake


def test_a_transient_failure_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Roughly one repository in a hundred fails once and then clones fine."""
    seen: list[list[str]] = []
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "subprocess.run",
        _run([subprocess.CalledProcessError(128, "git", stderr="early EOF"), None], seen),
    )
    assert clone_or_update("a/b", tmp_path) is True
    assert len(seen) == 2


def test_a_deleted_repository_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It will not come back, and the attempts are better spent elsewhere."""
    seen: list[list[str]] = []
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "subprocess.run",
        _run(
            [subprocess.CalledProcessError(128, "git", stderr="ERROR: Repository not found.")],
            seen,
        ),
    )
    assert clone_or_update("a/b", tmp_path) is False
    assert len(seen) == 1


def test_retries_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "subprocess.run",
        _run([subprocess.CalledProcessError(128, "git", stderr="RPC failed")] * 3, seen),
    )
    assert clone_or_update("a/b", tmp_path, attempts=3) is False
    assert len(seen) == 3


def test_a_half_written_clone_is_cleared_before_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the retry becomes a fetch into a directory git never finished."""
    dest = tmp_path / "a__b"
    dest.mkdir(parents=True)
    (dest / "partial.pack").write_bytes(b"junk")  # no .git: an interrupted clone

    seen: list[list[str]] = []
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "subprocess.run",
        _run([subprocess.CalledProcessError(128, "git", stderr="early EOF"), None], seen),
    )
    assert clone_or_update("a/b", tmp_path) is True
    assert seen[1][1] == "clone", "the retry must still be a clone, not a fetch"
