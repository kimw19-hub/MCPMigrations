"""Record types written by the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CommitRecord:
    repo: str
    sha: str
    # First parent, or "" at a root commit. Captured here because a compare link
    # has to start one commit before the episode, and resolving `sha^` later
    # needs the clone -- which an ephemeral harvest has already deleted.
    parent_sha: str
    date: str  # ISO 8601, UTC
    author_kind: str  # names and emails are deliberately not stored
    is_merge: bool
    message: str  # first line only
    signal_files: list[str]
    lines_added: int
    lines_deleted: int
    added: dict[str, int]
    removed: dict[str, int]
    migration_candidate: bool
    pin_below_2_added: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HeadRecord:
    repo: str
    head_sha: str
    signals: dict[str, int]
    static_era: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Episode:
    episode_id: str
    repo: str
    kind: str
    start: str
    end: str
    duration_days: int
    shas: list[str]
    lines_added: int
    lines_deleted: int
    author_kinds: list[str]
    deprecated_removed: list[str] = field(default_factory=list)
    revisions_added: list[str] = field(default_factory=list)
    revisions_removed: list[str] = field(default_factory=list)
    replacements: list[str] = field(default_factory=list)
    scope: str = "protocol_only"
    head_static_era: str | None = None

    @property
    def first_commit_url(self) -> str:
        return f"https://github.com/{self.repo}/commit/{self.shas[0]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
