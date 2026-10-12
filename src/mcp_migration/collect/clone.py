"""Cloning repositories that passed the inclusion criteria."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from mcp_migration.repos import repo_dir_name

log = logging.getLogger(__name__)


# A repository that is gone stays gone; retrying it only wastes the attempt.
GONE = ("repository not found", "could not read username", "access denied", "has disappeared")


def _is_gone(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in GONE)


def clone_or_update(slug: str, repos_root: Path, timeout: int = 1800, attempts: int = 3) -> bool:
    """Clone a public repository, or fetch into an existing clone.

    Clones use --no-checkout: mining reads history and the HEAD tree from the
    object database, never the working tree, and omitting it cut a 16-repository
    sample from 460 MB to 130 MB. Without it the full corpus would not fit on
    disk. `git fetch` rather than `git pull`, since there is no checkout to
    fast-forward.
    """
    dest = repos_root / repo_dir_name(slug)
    if (dest / ".git").exists():
        cmd = [
            "git",
            "-C",
            str(dest),
            "fetch",
            "--quiet",
            "origin",
            "+HEAD:refs/remotes/origin/HEAD",
        ]
    else:
        repos_root.mkdir(parents=True, exist_ok=True)
        cmd = [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            f"https://github.com/{slug}.git",
            str(dest),
        ]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # fail fast on deleted/private repos
    for attempt in range(1, attempts + 1):
        try:
            subprocess.run(
                cmd, check=True, env=env, timeout=timeout, capture_output=True, text=True
            )
            return True
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if _is_gone(stderr):
                log.warning("%s is gone: %s", slug, stderr)
                return False
        except subprocess.TimeoutExpired:
            stderr = "timed out"
        if attempt == attempts:
            log.warning("git failed for %s after %d attempts: %s", slug, attempts, stderr)
            return False
        # A partial clone left behind would make the next attempt a fetch
        # against a directory git never finished writing.
        if not (dest / ".git").exists() and dest.exists():
            import shutil

            shutil.rmtree(dest, ignore_errors=True)
        time.sleep(2 * attempt)
    return False
