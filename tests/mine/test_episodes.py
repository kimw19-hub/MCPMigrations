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
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"revision_literal:2025-06-18": 1},
            {"revision_literal:2025-03-26": 1},
        )
    ]
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


def test_a_revision_literal_that_only_appears_is_incidental() -> None:
    """A new date with nothing replaced is a fixture, a doc link or new code on an
    old revision, not a change in what the project speaks."""
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", {"revision_literal:2025-06-18": 1}, {})]
    assert build_episodes(commits)[0].kind == "incidental"


def test_moving_a_literal_without_changing_revision_is_incidental() -> None:
    """The same revision removed and re-added is a refactor or a moved line."""
    same = {"revision_literal:2025-06-18": 2}
    commits = [_commit("a", "2026-08-01T00:00:00+00:00", same, same)]
    assert build_episodes(commits)[0].kind == "incidental"


def test_a_v1_removal_needs_standalone_fastmcp_to_take_over() -> None:
    switch = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"py_standalone_fastmcp": 1},
            {"py_v1_fastmcp_import": 1},
        )
    ]
    assert build_episodes(switch)[0].kind == "v1_removal"


def test_a_v1_removal_with_nothing_taking_over_is_incidental() -> None:
    """Deleting a v1 example or reformatting an import is not leaving v1."""
    cleanup = [_commit("a", "2026-08-01T00:00:00+00:00", {}, {"py_v1_fastmcp_import": 1})]
    assert build_episodes(cleanup)[0].kind == "incidental"


def test_a_refactor_that_moves_v1_lines_is_incidental_even_with_fastmcp() -> None:
    moved = {"py_v1_fastmcp_import": 3}
    commits = [
        _commit("a", "2026-08-01T00:00:00+00:00", {**moved, "py_standalone_fastmcp": 1}, moved)
    ]
    assert build_episodes(commits)[0].kind == "incidental"


def test_a_v1_cleanup_is_incidental_even_when_a_revision_moves() -> None:
    """Literals move as noisily as imports inside v1 churn, so they rescue nothing."""
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"revision_literal:2026-07-28": 1},
            {"ts_v1_sdk_pkg": 1, "revision_literal:2025-06-18": 1},
        )
    ]
    assert build_episodes(commits)[0].kind == "incidental"


def test_mcperror_alone_is_not_evidence_of_v2() -> None:
    """Projects define their own MCPError; alone it must not make a v2 episode."""
    own_class = [_commit("a", "2026-08-01T00:00:00+00:00", {"py_v2_MCPError": 3}, {})]
    assert build_episodes(own_class)[0].kind == "incidental"


def test_mcperror_with_a_v2_import_is_still_an_adoption() -> None:
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"py_v2_MCPError": 1, "py_v2_mcpserver_import": 1},
            {},
        )
    ]
    assert build_episodes(commits)[0].kind == "v2_adoption"


def test_mcperror_does_not_hide_a_pin_below_two() -> None:
    commits = [
        _commit(
            "a", "2026-08-01T00:00:00+00:00", {"py_v2_MCPError": 1, "py_pin_mcp_below_2": 1}, {}
        )
    ]
    assert build_episodes(commits)[0].kind == "pin_below_2"


def test_mcperror_with_a_revision_literal_is_incidental() -> None:
    """New code carrying its own error class speaks whatever revision it was written for."""
    commits = [
        _commit(
            "a",
            "2026-08-01T00:00:00+00:00",
            {"py_v2_MCPError": 1, "revision_literal:2025-06-18": 1},
            {"revision_literal:2025-03-26": 1},
        )
    ]
    assert build_episodes(commits)[0].kind == "incidental"


def test_an_episode_records_its_own_outcome_not_the_state_at_head() -> None:
    """Pin in August, migrate in September: the August episode ended pinned."""
    from mcp_migration.mine.episodes import build_episodes

    def commit(day: str, added: dict[str, int], removed: dict[str, int]) -> dict[str, object]:
        return {
            "repo": "a/b",
            "sha": day,
            "date": f"2026-{day}T12:00:00+00:00",
            "is_merge": False,
            "author_kind": "human_or_unknown",
            "lines_added": 1,
            "lines_deleted": 1,
            "added": added,
            "removed": removed,
        }

    commits = [
        commit("01-10", {"py_v1_fastmcp_import": 1}, {}),
        commit("08-02", {"py_pin_mcp_below_2": 1}, {}),
        commit(
            "10-01",
            {"py_v2_mcpserver_import": 1},
            {"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1},
        ),
    ]
    head = {"static_era": "sdk_v2_default", "signals": {"py_v2_mcpserver_import": 1}}
    pin, migration = [
        e for e in build_episodes(commits, head) if e.kind in ("pin_below_2", "migration")
    ]
    assert pin.kind == "pin_below_2"
    assert (pin.era_before, pin.era_after) == ("sdk_v1", "sdk_v1_pinned_below_2")
    assert pin.head_static_era == "sdk_v2_default"  # what the old figures reported
    assert migration.kind == "migration"
    assert (migration.era_before, migration.era_after) == (
        "sdk_v1_pinned_below_2",
        "sdk_v2_default",
    )
