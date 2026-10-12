from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

GIT_ENV = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, Callable[[dict[str, str], str], None]]:
    """An empty repo named like a clone (``owner__repo``) plus a commit helper."""
    repo = tmp_path / "acme__weather-mcp"
    repo.mkdir()
    env = {**os.environ, **GIT_ENV}

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    run("init", "-q")

    def commit(files: dict[str, str], message: str) -> None:
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        run("add", "-A")
        run("commit", "-q", "-m", message)

    return repo, commit


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root, found by marker rather than by counting parents.

    Tests reading docs/ or paper/ previously counted `.parents`, which pointed
    at the wrong directory as soon as they moved into a subdirectory. Because
    those tests skip when the file is absent, the breakage read as a skip
    rather than a failure.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("no pyproject.toml above the test suite")
