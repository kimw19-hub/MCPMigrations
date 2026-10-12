"""Stratified sampling of episodes and the manual-labeling sheet."""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from mcp_migration.mine.classify import StaticEra
from mcp_migration.models import Episode

# Columns for manual coding; see docs/codebook.md for allowed values.
#
# Deliberately short. Every column costs two judgements per episode, so a
# dimension earns its place only if it is applicable often enough to be worth
# asking about. Measured on the sample, the four deprecated features were
# individually relevant to 3-4% of episodes and jointly to 6%, so they collapse
# into one column that stays blank for the other 94%; `replacement` and
# `state_handling` applied to fewer still and fold into it and `notes`.
# `scope` is largely implied by the end state, and `coder` repeated one value
# per row when the filename already identifies the coder.
CODEBOOK_COLUMNS = (
    "detector_correct",
    "end_state",
    "trigger",
    "author_kind_verified",
    "deprecated",
    "notes",
)
# Columns that carry a categorical judgement, and so are the ones Cohen's kappa
# describes. Free text never matches between coders, and reporting a kappa over
# it would understate agreement rather than measure it.
AGREEMENT_COLUMNS = tuple(c for c in CODEBOOK_COLUMNS if c != "notes")
# The coded end state each static era corresponds to. Declared here so that a
# new StaticEra cannot be introduced without giving coders a value for it, and
# so that end_state doubles as a check on the static detector.
END_STATE_BY_ERA = {
    StaticEra.SDK_V2_MODERN_FEATURES: "modern_only",
    StaticEra.MIXED_V1_V2: "dual_era",
    StaticEra.SDK_V2_DEFAULT: "sdk_v2_default",
    StaticEra.SDK_V1_PINNED_BELOW_2: "pinned_below_2",
    StaticEra.SDK_V1: "still_v1",
    StaticEra.STANDALONE_FASTMCP: "standalone_fastmcp",
    StaticEra.NO_OFFICIAL_SDK: "no_official_sdk",
}
# Outcomes a coder can observe that no snapshot of HEAD can show.
EXTRA_END_STATES = ("reverted", "abandoned")
# Allowed values for the remaining categorical columns, as docs/codebook.md
# lists them. The labeling app offers exactly these and rejects anything else.
DETECTOR_VERDICTS = ("yes", "no")
END_STATES = (*END_STATE_BY_ERA.values(), *EXTRA_END_STATES)
TRIGGERS = ("proactive", "user_issue", "bot_pr_breakage", "downstream_change", "unknown")
AUTHOR_KINDS_VERIFIED = ("human", "bot", "ai_agent_assisted")
# `deprecated` holds semicolon-separated feature:disposition pairs.
DEPRECATED_FEATURES = ("sampling", "roots", "logging", "subscriptions")
DISPOSITIONS = ("removed", "replaced", "kept")
PREFILLED_COLUMNS = (
    "episode_id",
    "repo",
    "kind",
    "start",
    "end",
    "n_commits",
    "head_static_era",
    "first_commit_url",
)


def stratified_sample(
    episodes: Iterable[Episode], per_stratum: int, seed: int = 42
) -> list[Episode]:
    """Sample up to ``per_stratum`` episodes per (kind, head_static_era)."""
    strata: dict[tuple[str, str], list[Episode]] = defaultdict(list)
    for ep in episodes:
        strata[(ep.kind, ep.head_static_era or "unknown")].append(ep)
    rng = random.Random(seed)
    sample: list[Episode] = []
    for key in sorted(strata):
        group = sorted(strata[key], key=lambda e: e.episode_id)
        sample.extend(rng.sample(group, min(per_stratum, len(group))))
    return sample


def write_labeling_sheet(path: Path, episodes: Sequence[Episode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow((*PREFILLED_COLUMNS, *CODEBOOK_COLUMNS))
        for ep in episodes:
            writer.writerow(
                (
                    ep.episode_id,
                    ep.repo,
                    ep.kind,
                    ep.start,
                    ep.end,
                    len(ep.shas),
                    ep.head_static_era or "",
                    ep.first_commit_url,
                    *([""] * len(CODEBOOK_COLUMNS)),
                )
            )
