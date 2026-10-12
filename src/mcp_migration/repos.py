"""Repository identifiers and seed-file parsing.

Pure functions with no I/O beyond reading a seed file, so that mining can
map a clone directory back to a slug without importing the cloning code.
"""

from __future__ import annotations

import re
from pathlib import Path

_URL_RX = re.compile(r"github\.com[/:]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")
_SLUG_RX = re.compile(r"^([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)$")
_RESERVED_OWNERS = frozenset(
    {"orgs", "topics", "features", "sponsors", "settings", "apps", "marketplace", "repos", "users"}
)
DIR_SEPARATOR = "__"  # GitHub owners cannot contain underscores, so this is reversible


def _clean(owner: str, name: str) -> str | None:
    name = name.removesuffix(".git")
    if owner.lower() in _RESERVED_OWNERS or name in {"", ".", ".."}:
        return None
    return f"{owner}/{name}".lower()


def normalize_repo(text: str) -> str | None:
    """Return ``owner/repo`` (lower-case) from a URL or slug, else ``None``."""
    text = text.strip()
    match = _URL_RX.search(text) or _SLUG_RX.match(text)
    return _clean(*match.groups()) if match else None


def extract_repos(text: str) -> set[str]:
    """Find every GitHub repository referenced in arbitrary text (JSON, CSV, lists)."""
    found = {slug for m in _URL_RX.finditer(text) if (slug := _clean(*m.groups()))}
    for line in text.splitlines():
        match = _SLUG_RX.match(line.strip())
        if match and (slug := _clean(*match.groups())):
            found.add(slug)
    return found


def load_seed_file(path: Path) -> set[str]:
    return extract_repos(path.read_text(encoding="utf-8", errors="ignore"))


def repo_dir_name(slug: str) -> str:
    return slug.replace("/", DIR_SEPARATOR, 1)


def slug_from_dir_name(name: str) -> str:
    return name.replace(DIR_SEPARATOR, "/", 1)
