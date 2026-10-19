"""Filesystem layout and environment settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_DATA_DIR = Path("data")
DEFAULT_QUERIES_FILE = Path("config/queries.toml")
DEFAULT_SINCE = date(2024, 11, 1)  # first public MCP revision


def github_token() -> str | None:
    """Return the first GitHub token from the environment, if set."""
    tokens = github_tokens()
    return tokens[0] if tokens else None


def github_tokens() -> list[str]:
    """Return every GitHub token available for rotation, in order.

    Reads ``GITHUB_TOKENS`` (comma- or whitespace-separated) and
    ``GITHUB_TOKEN``, deduplicating while preserving order so the same
    credential listed twice does not look like extra capacity. Rotation pays
    off only across tokens from different accounts, since GitHub keys its rate
    limits to the token's owner.
    """
    sources = (os.environ.get("GITHUB_TOKENS"), os.environ.get("GITHUB_TOKEN"))
    raw = " ".join(value for value in sources if value)
    seen: set[str] = set()
    tokens: list[str] = []
    for token in re.split(r"[,\s]+", raw):
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


@dataclass(frozen=True)
class Paths:
    """All pipeline inputs and outputs, relative to one data root."""

    root: Path = DEFAULT_DATA_DIR

    @property
    def search_dir(self) -> Path:
        """One JSONL file per query, so a long run can be resumed."""
        return self.root / "raw" / "search"

    @property
    def search_hits(self) -> Path:
        """Merged view of every query's hits."""
        return self.root / "raw" / "search_hits.jsonl"

    @property
    def repo_list(self) -> Path:
        return self.root / "interim" / "repos.txt"

    @property
    def repo_meta(self) -> Path:
        """GraphQL metadata for every seeded repository."""
        return self.root / "interim" / "repo_meta.jsonl"

    @property
    def clone_list(self) -> Path:
        """Repositories surviving the inclusion criteria; the input to `clone`."""
        return self.root / "interim" / "repos_to_clone.txt"

    @property
    def repos(self) -> Path:
        return self.root / "repos"

    @property
    def commits_dir(self) -> Path:
        return self.root / "interim" / "commits"

    @property
    def heads_dir(self) -> Path:
        return self.root / "interim" / "heads"

    @property
    def episodes(self) -> Path:
        return self.root / "processed" / "episodes.jsonl"

    @property
    def incidental_episodes(self) -> Path:
        """Signal clusters the kind rules reject, kept so they can be audited."""
        return self.root / "processed" / "episodes_incidental.jsonl"

    @property
    def label_report(self) -> Path:
        """Precision and agreement computed from the coded sheets."""
        return self.root / "processed" / "label_report.json"

    @property
    def harvest_outcomes(self) -> Path:
        """Repositories a harvest could not mine, and why.

        A log line is not a result. The paper reports how many repositories
        were attempted and dropped, and by which criterion, so the reasons
        have to survive the run as data.
        """
        return self.root / "interim" / "harvest_outcomes.jsonl"

    @property
    def qualitative(self) -> Path:
        """Counts coded from pin commit subjects and breakage issues."""
        return self.root / "processed" / "qualitative.json"

    @property
    def ts_root(self) -> Path:
        """The TypeScript mining pass, HEAD and application scans, and their manual audits."""
        return self.root / "ts"

    def labeling_round(self, round_: int) -> Path:
        """One labeling round's directory; see data/labeling/README.md."""
        return self.root / "labeling" / f"round{round_}"

    def labeling_rounds(self) -> list[int]:
        """The rounds that have a drawn sheet, oldest first."""
        found = (d.name.removeprefix("round") for d in (self.root / "labeling").glob("round*"))
        return sorted(int(n) for n in found if n.isdigit() and self.labeling_sheet(int(n)).exists())

    def labeling_sheet(self, round_: int = 1) -> Path:
        """The blank sheet drawn for a round."""
        return self.labeling_round(round_) / "sample.csv"

    def coder_sheet(self, coder: str, round_: int = 1) -> Path:
        """One coder's copy of a round's sheet; see docs/codebook.md."""
        return self.labeling_round(round_) / f"coder{coder}.csv"

    @property
    def github_cache(self) -> Path:
        """Commit details the labeling app fetched from GitHub."""
        return self.root / "interim" / "github-cache"
