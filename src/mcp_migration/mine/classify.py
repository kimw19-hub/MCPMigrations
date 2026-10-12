"""Rule-based labels derived from signal counts and commit metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from mcp_migration.mine.signals import (
    MODERN_FEATURE_KEYS,
    PIN_BELOW_2_KEY,
    V1_REMOVED_KEYS,
    V1_SDK_KEYS,
    V2_ADDED_KEYS,
    V2_SDK_KEYS,
)

_BOT_RX = re.compile(r"\[bot\]|dependabot|renovate", re.IGNORECASE)
_AGENT_RX = re.compile(
    r"co-authored-by:[^\n]*(claude|copilot|codex|cursor|devin|jules)|generated with \[?claude code",
    re.IGNORECASE,
)


class AuthorKind(StrEnum):
    BOT = "bot"
    AI_AGENT_ASSISTED = "ai_agent_assisted"  # lower bound: agents without trailers are missed
    HUMAN_OR_UNKNOWN = "human_or_unknown"


class StaticEra(StrEnum):
    """Era inferred from source at HEAD.

    SDK v2 servers also answer the legacy handshake by default, so "modern-only"
    cannot be decided statically; confirm it with a runtime probe.
    """

    MIXED_V1_V2 = "mixed_v1_v2"
    SDK_V2_MODERN_FEATURES = "sdk_v2_modern_features"
    SDK_V2_DEFAULT = "sdk_v2_default"
    SDK_V1_PINNED_BELOW_2 = "sdk_v1_pinned_below_2"
    SDK_V1 = "sdk_v1"
    STANDALONE_FASTMCP = "standalone_fastmcp"
    NO_OFFICIAL_SDK = "no_official_sdk"


def _any(counts: Mapping[str, int], keys: tuple[str, ...]) -> bool:
    return any(counts.get(k, 0) > 0 for k in keys)


def author_kind(name: str | None, email: str | None, message: str | None) -> AuthorKind:
    if _BOT_RX.search(name or "") or _BOT_RX.search(email or ""):
        return AuthorKind.BOT
    if _AGENT_RX.search(message or "") or (name or "").strip().lower() == "copilot":
        return AuthorKind.AI_AGENT_ASSISTED
    return AuthorKind.HUMAN_OR_UNKNOWN


def is_migration_candidate(added: Mapping[str, int], removed: Mapping[str, int]) -> bool:
    """A single commit that introduces v2 signals and removes v1 signals."""
    return _any(added, V2_ADDED_KEYS) and _any(removed, V1_REMOVED_KEYS)


def static_era(counts: Mapping[str, int]) -> StaticEra:
    v1 = _any(counts, V1_SDK_KEYS)
    v2 = _any(counts, V2_SDK_KEYS)
    if v1 and v2:
        return StaticEra.MIXED_V1_V2  # compat shim or partial migration
    if v2 and _any(counts, MODERN_FEATURE_KEYS):
        return StaticEra.SDK_V2_MODERN_FEATURES
    if v2:
        return StaticEra.SDK_V2_DEFAULT
    if counts.get(PIN_BELOW_2_KEY, 0) > 0:
        return StaticEra.SDK_V1_PINNED_BELOW_2
    if v1:
        return StaticEra.SDK_V1
    if counts.get("py_standalone_fastmcp", 0) > 0:
        return StaticEra.STANDALONE_FASTMCP
    return StaticEra.NO_OFFICIAL_SDK
