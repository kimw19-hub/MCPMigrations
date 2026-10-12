from typing import Any

from mcp_migration.mine.episodes import build_episodes


def _commit(sha: str, date: str, added: dict[str, int], removed: dict[str, int]) -> dict[str, Any]:
    return {
        "repo": "acme/server",
        "sha": sha,
        "date": date,
        "author_kind": "human_or_unknown",
        "is_merge": False,
        "lines_added": 10,
        "lines_deleted": 5,
        "added": added,
        "removed": removed,
    }


def test_groups_by_gap_and_labels_kind() -> None:
    commits = [
        _commit("a", "2026-08-01T00:00:00+00:00", {"py_v2_mcpserver_import": 1}, {}),
        _commit("b", "2026-08-05T00:00:00+00:00", {}, {"py_v1_fastmcp_import": 1, "dep_roots": 1}),
        _commit("c", "2026-10-01T00:00:00+00:00", {"py_pin_mcp_below_2": 1}, {}),
        _commit("d", "2026-10-02T00:00:00+00:00", {"dep_sampling": 1}, {}),  # unrelated
    ]
    episodes = build_episodes(commits, {"static_era": "sdk_v2_default"}, max_gap_days=30)

    assert [e.kind for e in episodes] == ["migration", "pin_below_2"]
    first = episodes[0]
    assert first.shas == ["a", "b"]
    assert first.duration_days == 4
    assert first.deprecated_removed == ["dep_roots"]
    assert first.head_static_era == "sdk_v2_default"


def test_no_related_commits() -> None:
    assert build_episodes([_commit("x", "2026-08-01T00:00:00+00:00", {"dep_logging": 1}, {})]) == []


def _commit(
    sha: str, date: str, added: dict[str, int], removed: dict[str, int]
) -> dict[str, object]:
    return {
        "repo": "a/b",
        "sha": sha,
        "date": date,
        "author_kind": "human_or_unknown",
        "is_merge": False,
        "lines_added": 1,
        "lines_deleted": 0,
        "added": added,
        "removed": removed,
    }


def test_scope_is_sdk_major_only_when_no_protocol_marker_moves() -> None:
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {"py_v2_mcpserver_import": 1}, {})]
    assert build_episodes(commits)[0].scope == "sdk_major_only"


def test_scope_is_protocol_only_without_an_sdk_change() -> None:
    """A project tracking the protocol without depending on the official SDK."""
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {"revision_literal:2026-07-28": 1}, {})]
    assert build_episodes(commits)[0].scope == "protocol_only"


def test_scope_is_both_when_the_sdk_and_a_revision_move_together() -> None:
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"py_v2_mcpserver_import": 1, "revision_literal:2026-07-28": 1},
            {},
        )
    ]
    assert build_episodes(commits)[0].scope == "sdk_and_protocol"


def test_scope_sees_signals_removed_as_well_as_added() -> None:
    """Dropping the v1 import is an SDK-generation change even with nothing added."""
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {}, {"py_v1_fastmcp_import": 1})]
    assert build_episodes(commits)[0].scope == "sdk_major_only"


def test_a_pin_below_two_is_an_sdk_change_not_a_protocol_one() -> None:
    """Capping at mcp<2 is a statement about the SDK major version."""
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {"py_pin_mcp_below_2": 1}, {})]
    episode = build_episodes(commits)[0]
    assert episode.kind == "pin_below_2"
    assert episode.scope == "sdk_major_only"


def test_a_revision_change_is_never_scoped_sdk_only() -> None:
    """By construction it moved a revision literal, so the protocol moved."""
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {"revision_literal:2025-06-18": 1}, {})]
    episode = build_episodes(commits)[0]
    assert episode.kind == "revision_change"
    assert episode.scope != "sdk_major_only"


def test_a_replacement_needs_both_sides_in_the_same_episode() -> None:
    """Dropping sampling and adopting InputRequiredResult together is evidence
    one replaced the other; doing so months apart is not."""
    together = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"modern_input_required": 1},
            {"dep_sampling": 1},
        )
    ]
    assert build_episodes(together)[0].replacements == ["mrtr_input_required"]

    apart = [
        # each must be migration-related on its own, or no episode is built
        _commit(
            "a",
            "2026-01-01T00:00:00+00:00",
            {},
            {"dep_sampling": 1, "py_v1_fastmcp_import": 1},
        ),
        _commit("b", "2026-08-01T00:00:00+00:00", {"modern_input_required": 1}, {}),
    ]
    episodes = build_episodes(apart)
    assert len(episodes) == 2
    assert all(e.replacements == [] for e in episodes)


def test_an_episode_can_carry_more_than_one_replacement() -> None:
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"modern_input_required": 1, "modern_server_discover": 1},
            {"dep_sampling": 1, "legacy_initialized_notif": 1},
        )
    ]
    assert build_episodes(commits)[0].replacements == ["mrtr_input_required", "server_discover"]


def test_removing_a_feature_without_a_successor_is_not_a_replacement() -> None:
    commits = [
        _commit(
            "a", "2026-08-01T00:00:00+00:00", {}, {"dep_sampling": 1, "py_v1_fastmcp_import": 1}
        )
    ]
    episode = build_episodes(commits)[0]
    assert episode.deprecated_removed == ["dep_sampling"]
    assert episode.replacements == []
